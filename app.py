import os, sqlite3, uuid, json
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_cors import CORS
import jwt
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import requests

APP_DIR=os.path.dirname(os.path.abspath(__file__))
DB_PATH=os.getenv('DATABASE_PATH', os.path.join(APP_DIR,'sov.sqlite3'))
JWT_SECRET=os.getenv('JWT_SECRET','')
GOOGLE_CLIENT_ID=os.getenv('GOOGLE_CLIENT_ID','')
ADMIN_EMAIL=os.getenv('ADMIN_EMAIL','').strip().lower()
MIN_PAYOUT_USD=90.0
WITHDRAWAL_FEE_PERCENT=50.0
PAYPAL_CLIENT_ID=os.getenv('PAYPAL_CLIENT_ID','').strip()
PAYPAL_CLIENT_SECRET=os.getenv('PAYPAL_CLIENT_SECRET','').strip()
PAYPAL_MODE=os.getenv('PAYPAL_MODE','sandbox').strip().lower()
PAYPAL_BASE_URL='https://api-m.paypal.com' if PAYPAL_MODE == 'live' else 'https://api-m.sandbox.paypal.com'
PAYPAL_CURRENCY=os.getenv('PAYPAL_CURRENCY','USD').strip().upper()
FRONTEND_ORIGIN=os.getenv('FRONTEND_ORIGIN','https://scorpio-octavious-vibe-web-lit.onrender.com').strip().rstrip('/')
BOOST_PACKAGES={
    'boost_10': {'amount': Decimal('10.00'), 'target_views': 10000},
    'boost_20': {'amount': Decimal('20.00'), 'target_views': 25000},
    'boost_35': {'amount': Decimal('35.00'), 'target_views': 50000},
    'boost_60': {'amount': Decimal('60.00'), 'target_views': 100000},
    'boost_250': {'amount': Decimal('250.00'), 'target_views': 500000},
}

app = Flask(__name__)

# Allow the configured SOV Lite frontend origins.
# Defaults to "*" only when CORS_ORIGINS is not configured.
_raw_origins = os.getenv("CORS_ORIGINS", "*")

_allowed_origins = [
    origin.strip().rstrip("/")
    for origin in _raw_origins.split(",")
    if origin.strip()
]

CORS(
    app,
    resources={r"/api/*": {"origins": _allowed_origins}},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    supports_credentials=False,
    max_age=600,
)

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
    cols={r[1] for r in con.execute('PRAGMA table_info(campaigns)').fetchall()}
    if 'paypal_order_id' not in cols: con.execute('ALTER TABLE campaigns ADD COLUMN paypal_order_id TEXT')
    if 'paypal_capture_id' not in cols: con.execute('ALTER TABLE campaigns ADD COLUMN paypal_capture_id TEXT')
    if 'payment_provider' not in cols: con.execute("ALTER TABLE campaigns ADD COLUMN payment_provider TEXT NOT NULL DEFAULT 'none'")
    con.execute('CREATE UNIQUE INDEX IF NOT EXISTS campaigns_paypal_order_idx ON campaigns(paypal_order_id)')
    con.execute('CREATE UNIQUE INDEX IF NOT EXISTS campaigns_paypal_capture_idx ON campaigns(paypal_capture_id)')
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
def health(): return jsonify({'status':'ok','service':'sov-lite-api','time':now(),'auth_configured':bool(GOOGLE_CLIENT_ID and JWT_SECRET),'paypal_configured':paypal_ready(),'paypal_mode':PAYPAL_MODE if paypal_ready() else 'not_configured'})
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

def paypal_ready():
    return bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET and PAYPAL_MODE in ('sandbox','live'))

def paypal_access_token():
    if not paypal_ready():
        raise RuntimeError('PayPal is not configured. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in Render.')
    response=requests.post(PAYPAL_BASE_URL + '/v1/oauth2/token',
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET), data={'grant_type':'client_credentials'},
        headers={'Accept':'application/json','Accept-Language':'en_US'}, timeout=20)
    if response.status_code >= 400:
        app.logger.error('PayPal OAuth failed with status %s', response.status_code)
        raise RuntimeError('PayPal authentication failed. Check the configured credentials and mode.')
    return response.json()['access_token']

def paypal_api(method, path, token, payload=None, request_id=None):
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Prefer':'return=representation'}
    if request_id: headers['PayPal-Request-Id']=request_id
    response=requests.request(method, PAYPAL_BASE_URL+path, json=payload, headers=headers, timeout=25)
    if response.status_code >= 400:
        app.logger.error('PayPal API %s failed with status %s', path, response.status_code)
        raise RuntimeError('PayPal could not complete this request. No campaign was activated.')
    return response.json() if response.content else {}

@app.get('/api/boost/packages')
def boost_packages():
    return jsonify({'currency':PAYPAL_CURRENCY,'packages':[
        {'code':code,'amount_usd':str(pkg['amount']),'target_views':pkg['target_views']}
        for code,pkg in BOOST_PACKAGES.items()
    ],'paypal_configured':paypal_ready(),'paypal_mode':PAYPAL_MODE if paypal_ready() else 'not_configured'})

@app.post('/api/boost/paypal/create-order')
@login_required
def boost_paypal_create_order():
    if not paypal_ready():
        return error('PayPal checkout is not configured yet. Add PayPal API credentials in Render before accepting real payments.',503)
    body=request.get_json(silent=True) or {}
    package_code=str(body.get('package_code','')).strip()
    pkg=BOOST_PACKAGES.get(package_code)
    if not pkg: return error('Choose a valid Boost Ads package.')
    campaign_id=str(uuid.uuid4())
    con=db()
    con.execute('''INSERT INTO campaigns(id,user_id,package_code,amount_usd,target_views,payment_status,delivery_status,created_at,payment_provider)
        VALUES(?,?,?,?,?,?,?,?,?)''',
        (campaign_id,g.user['id'],package_code,float(pkg['amount']),pkg['target_views'],'pending','not_started',now(),'paypal'))
    con.commit()
    try:
        token=paypal_access_token()
        order=paypal_api('POST','/v2/checkout/orders',token,{
            'intent':'CAPTURE',
            'purchase_units':[{'reference_id':campaign_id,'custom_id':campaign_id,
                'description':f'SOV Lite Boost Ads - {pkg["target_views"]} target views',
                'amount':{'currency_code':PAYPAL_CURRENCY,'value':f'{pkg["amount"]:.2f}'}}],
            'application_context':{'brand_name':'Scorpio Octavious Vibe Lite','user_action':'PAY_NOW',
                'shipping_preference':'NO_SHIPPING',
                'return_url':FRONTEND_ORIGIN+'/boost-ads.html?paypal=return',
                'cancel_url':FRONTEND_ORIGIN+'/boost-ads.html?paypal=cancel'}
        },request_id=campaign_id)
        con.execute('UPDATE campaigns SET paypal_order_id=? WHERE id=?',(order['id'],campaign_id));con.commit()
        approve=next((x.get('href') for x in order.get('links',[]) if x.get('rel')=='approve'),None)
        if not approve: return error('PayPal order created but no approval link was returned.',502)
        return jsonify({'campaign_id':campaign_id,'order_id':order['id'],'approve_url':approve,'status':'pending',
            'amount_usd':str(pkg['amount']),'target_views':pkg['target_views']}),201
    except Exception as exc:
        app.logger.exception('Failed creating PayPal order')
        con.execute('UPDATE campaigns SET payment_status=? WHERE id=?',('failed',campaign_id));con.commit()
        return error(str(exc) if isinstance(exc,RuntimeError) else 'Unable to create PayPal order. Check server logs.',502)

@app.post('/api/boost/paypal/capture')
@login_required
def boost_paypal_capture_order():
    if not paypal_ready(): return error('PayPal is not configured.',503)
    body=request.get_json(silent=True) or {}
    order_id=str(body.get('order_id','')).strip()
    campaign_id=str(body.get('campaign_id','')).strip()
    if not order_id or not campaign_id: return error('order_id and campaign_id are required.')
    con=db()
    campaign=con.execute('SELECT * FROM campaigns WHERE id=? AND user_id=?',(campaign_id,g.user['id'])).fetchone()
    if not campaign or campaign['payment_provider']!='paypal' or campaign['paypal_order_id']!=order_id:
        return error('Order does not match this account campaign.',404)
    if campaign['payment_status']=='paid':
        return jsonify({'campaign_id':campaign_id,'status':'paid','delivery_status':campaign['delivery_status'],'message':'Payment was already recorded.'})
    try:
        token=paypal_access_token()
        result=paypal_api('POST',f'/v2/checkout/orders/{order_id}/capture',token,{},request_id='capture-'+campaign_id)
        if result.get('status')!='COMPLETED': return error('PayPal has not confirmed a completed payment.',409)
        units=result.get('purchase_units') or []
        captures=[c for unit in units for c in (unit.get('payments',{}).get('captures') or [])]
        capture=next((c for c in captures if c.get('status')=='COMPLETED'),None)
        if not capture: return error('No completed PayPal capture was found.',409)
        amount=capture.get('amount') or {}
        if amount.get('currency_code')!=PAYPAL_CURRENCY or Decimal(str(amount.get('value','0'))) != Decimal(str(campaign['amount_usd'])):
            app.logger.error('PayPal amount mismatch for campaign %s', campaign_id)
            return error('Captured payment amount or currency does not match the campaign.',409)
        capture_id=capture.get('id')
        if not capture_id: return error('PayPal capture ID is missing.',409)
        con.execute('UPDATE campaigns SET payment_status=?,delivery_status=?,paypal_capture_id=? WHERE id=? AND payment_status=?',
            ('paid','queued',capture_id,campaign_id,'pending'))
        con.commit()
        fresh=con.execute('SELECT payment_status,delivery_status FROM campaigns WHERE id=?',(campaign_id,)).fetchone()
        return jsonify({'campaign_id':campaign_id,'status':fresh['payment_status'],'delivery_status':fresh['delivery_status'],
            'message':'Payment verified. Campaign is queued; actual view delivery still requires an ad-delivery system.'})
    except Exception as exc:
        app.logger.exception('PayPal capture failed')
        return error(str(exc) if isinstance(exc,RuntimeError) else 'Unable to verify PayPal capture. Campaign was not activated.',502)

@app.get('/api/boost/campaigns')
@login_required
def boost_campaigns():
    rows=db().execute('SELECT id,package_code,amount_usd,target_views,payment_status,delivery_status,created_at FROM campaigns WHERE user_id=? ORDER BY created_at DESC LIMIT 100',(g.user['id'],)).fetchall()
    return jsonify({'campaigns':[dict(row) for row in rows]})

@app.post('/api/boost/crypto/create-intent')
@login_required
def boost_crypto_create_intent():
    return error('Crypto campaign payments are not enabled yet. Configure supported network/token contracts, receiving address, RPC/indexer, confirmations, and server-side transaction verification before accepting funds.',503)

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
