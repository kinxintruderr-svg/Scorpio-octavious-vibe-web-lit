import os, sqlite3, uuid, json
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_cors import CORS
import jwt
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

APP_DIR=os.path.dirname(os.path.abspath(__file__))
DB_PATH=os.getenv('DATABASE_PATH', os.path.join(APP_DIR,'sov.sqlite3'))
JWT_SECRET=os.getenv('JWT_SECRET','')
GOOGLE_CLIENT_ID=os.getenv('GOOGLE_CLIENT_ID','')
ADMIN_EMAIL=os.getenv('ADMIN_EMAIL','').strip().lower()
MIN_PAYOUT_USD=90.0
WITHDRAWAL_FEE_PERCENT=50.0
app=Flask(__name__)
CORS(app, origins=os.getenv('CORS_ORIGINS','*').split(','), supports_credentials=False)

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    if 'db' not in g:
        g.db=sqlite3.connect(DB_PATH, timeout=20)
        g.db.row_factory=sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db
@app.teardown_appcontext
def close_db(exc):
    conn=g.pop('db',None)
    if conn is not None: conn.close()
def init_db():
    con=sqlite3.connect(DB_PATH)
    con.executescript('''
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, google_sub TEXT UNIQUE NOT NULL, email TEXT NOT NULL, name TEXT NOT NULL, picture TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), amount_sov REAL NOT NULL, type TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', external_ref TEXT UNIQUE, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS withdrawals(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), amount_usd REAL NOT NULL, fee_usd REAL NOT NULL, net_usd REAL NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), provider TEXT NOT NULL, provider_ref TEXT UNIQUE NOT NULL, reward_sov REAL NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), package_code TEXT NOT NULL, amount_usd REAL NOT NULL, target_views INTEGER NOT NULL, payment_status TEXT NOT NULL, delivery_status TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS ledger_user_idx ON ledger(user_id,created_at);
    CREATE INDEX IF NOT EXISTS withdrawals_user_idx ON withdrawals(user_id,created_at);
    ''')
    con.commit();con.close()
def error(msg,status=400): return jsonify({'error':msg}),status
def current_user():
    auth=request.headers.get('Authorization','')
    if not auth.startswith('Bearer '): return None
    if not JWT_SECRET: return None
    try:
        payload=jwt.decode(auth[7:],JWT_SECRET,algorithms=['HS256'],options={'require':['sub','exp']})
        return db().execute('SELECT * FROM users WHERE id=?',(payload['sub'],)).fetchone()
    except Exception: return None
def login_required(fn):
    @wraps(fn)
    def wrapped(*a,**kw):
        user=current_user()
        if not user: return error('Sign in with Google first; token invalid or backend auth is not configured.',401)
        g.user=user
        return fn(*a,**kw)
    return wrapped
def admin_required(fn):
    @wraps(fn)
    def wrapped(*a,**kw):
        user=current_user()
        if not user: return error('Authentication required.',401)
        if not ADMIN_EMAIL or user['email'].lower()!=ADMIN_EMAIL: return error('Administrator access denied.',403)
        g.user=user
        return fn(*a,**kw)
    return wrapped
@app.get('/api/health')
def health(): return jsonify({'status':'ok','service':'sov-lite-api','time':now(),'auth_configured':bool(GOOGLE_CLIENT_ID and JWT_SECRET)})
@app.post('/api/auth/google')
def google_login():
    if not GOOGLE_CLIENT_ID or not JWT_SECRET: return error('Google authentication is not configured on the server.',503)
    body=request.get_json(silent=True) or {}; credential=body.get('credential','')
    if not credential: return error('Google credential is required.')
    try: info=id_token.verify_oauth2_token(credential,google_requests.Request(),GOOGLE_CLIENT_ID)
    except Exception: return error('Google credential verification failed.',401)
    if info.get('email_verified') is not True: return error('Google email is not verified.',403)
    con=db(); sub=info['sub']; email=info.get('email','').lower(); name=info.get('name') or email.split('@')[0]; picture=info.get('picture','')
    user=con.execute('SELECT * FROM users WHERE google_sub=?',(sub,)).fetchone()
    if user: con.execute('UPDATE users SET email=?,name=?,picture=? WHERE id=?',(email,name,picture,user['id'])); uid=user['id']
    else:
        uid=str(uuid.uuid4());con.execute('INSERT INTO users(id,google_sub,email,name,picture,created_at) VALUES(?,?,?,?,?,?)',(uid,sub,email,name,picture,now()))
    con.commit(); token=jwt.encode({'sub':uid,'email':email,'iat':datetime.now(timezone.utc),'exp':datetime.now(timezone.utc)+timedelta(hours=12)},JWT_SECRET,algorithm='HS256')
    return jsonify({'access_token':token,'token_type':'Bearer','expires_in':43200,'user':{'id':uid,'email':email,'name':name,'picture':picture}})
@app.get('/api/me')
@login_required
def me(): return jsonify({'id':g.user['id'],'email':g.user['email'],'name':g.user['name'],'picture':g.user['picture']})
@app.get('/api/wallet')
@login_required
def wallet():
    con=db(); row=con.execute('SELECT COALESCE(SUM(amount_sov),0) AS balance FROM ledger WHERE user_id=? AND status="confirmed"',(g.user['id'],)).fetchone()
    tx=con.execute('SELECT type,amount_sov,status,detail,created_at FROM ledger WHERE user_id=? ORDER BY created_at DESC LIMIT 100',(g.user['id'],)).fetchall()
    return jsonify({'balance_sov':round(float(row['balance']),8),'transactions':[dict(x) for x in tx]})
@app.post('/api/wallet/withdrawals')
@login_required
def create_withdrawal():
    body=request.get_json(silent=True) or {}
    try: amount=round(float(body.get('amount_usd')),2)
    except (TypeError,ValueError): return error('amount_usd must be a number.')
    method=str(body.get('method','')).strip()
    if amount<MIN_PAYOUT_USD: return error('Minimum withdrawal request is US$90.00.')
    if method not in ('PayPal','Crypto wallet'): return error('Unsupported payout method.')
    wid=str(uuid.uuid4()); fee=round(amount*WITHDRAWAL_FEE_PERCENT/100,2); net=round(amount-fee,2)
    db().execute('INSERT INTO withdrawals(id,user_id,amount_usd,fee_usd,net_usd,method,status,created_at) VALUES(?,?,?,?,?,?,?,?)',(wid,g.user['id'],amount,fee,net,method,'pending_review',now()));db().commit()
    return jsonify({'request_id':wid,'amount_usd':amount,'fee_usd':fee,'net_usd':net,'status':'pending_review','message':'Request recorded. No payment has been sent.'}),201
@app.get('/api/earn/summary')
@login_required
def earn_summary():
    con=db(); bal=con.execute('SELECT COALESCE(SUM(amount_sov),0) AS b FROM ledger WHERE user_id=? AND status="confirmed" AND type="tapjoy_reward"',(g.user['id'],)).fetchone()['b']
    count=con.execute('SELECT COUNT(*) AS n FROM offers WHERE user_id=?',(g.user['id'],)).fetchone()['n']
    return jsonify({'reward_balance_sov':round(float(bal),8),'verified_offers_completed':count,'provider':'Tapjoy','status':'callback integration must be configured'})
# Fail closed until the provider-specific signed callback format and shared secret are configured.
@app.post('/api/tapjoy/callback')
def tapjoy_callback():
    secret=os.getenv('TAPJOY_CALLBACK_SECRET','')
    if not secret: return error('Tapjoy callback is disabled until TAPJOY_CALLBACK_SECRET and provider verification are configured.',503)
    # Provider-specific signature validation must match the account's Tapjoy callback configuration.
    # Never credit rewards from unsigned/unverified requests. Configure the exact signed payload format before enabling.
    return error('Tapjoy callback verifier is intentionally disabled until the account-specific signature scheme is configured.',503)
@app.post('/api/admin/ledger/credit')
@admin_required
def admin_credit():
    # Controlled manual ledger adjustment; must include an auditable reason and unique reference.
    body=request.get_json(silent=True) or {}
    uid=str(body.get('user_id','')).strip(); ref=str(body.get('external_ref','')).strip(); detail=str(body.get('detail','')).strip()
    try: amount=round(float(body.get('amount_sov')),8)
    except (TypeError,ValueError): return error('amount_sov must be a number.')
    if not uid or not ref or not detail or amount<=0: return error('user_id, positive amount_sov, unique external_ref and detail are required.')
    lid=str(uuid.uuid4())
    try:
        db().execute('INSERT INTO ledger(id,user_id,amount_sov,type,status,detail,external_ref,created_at) VALUES(?,?,?,?,?,?,?,?)',(lid,uid,amount,'admin_adjustment','confirmed',detail,ref,now()));db().commit()
    except sqlite3.IntegrityError: return error('User or external reference is invalid or already used.',409)
    return jsonify({'ledger_id':lid,'status':'confirmed'}),201
@app.get('/api/admin/summary')
@admin_required
def admin_summary():
    con=db(); return jsonify({'users':con.execute('SELECT COUNT(*) n FROM users').fetchone()['n'],'withdrawals_pending':con.execute('SELECT COUNT(*) n FROM withdrawals WHERE status="pending_review"').fetchone()['n'],'verified_offers':con.execute('SELECT COUNT(*) n FROM offers').fetchone()['n'],'campaigns':con.execute('SELECT COUNT(*) n FROM campaigns').fetchone()['n']})
@app.errorhandler(500)
def internal_error(e): return jsonify({'error':'Internal server error'}),500
init_db()
if __name__=='__main__':
    if not JWT_SECRET: print('WARNING: Set JWT_SECRET before exposing this API publicly.')
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')))
