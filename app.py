import os, sqlite3, hashlib, hmac, json, secrets
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import requests

app = Flask(__name__, static_folder='static')
CORS(app, resources={r'/api/*': {'origins': '*'}})
APP_SECRET = os.getenv('APP_SECRET', '')
TAPJOY_SECRET = os.getenv('TAPJOY_SECRET', '')
TAPJOY_WEB_SDK_KEY = os.getenv('TAPJOY_WEB_SDK_KEY', '')
PAYPAL_CLIENT_ID = os.getenv('PAYPAL_CLIENT_ID', '')
PAYPAL_CLIENT_SECRET = os.getenv('PAYPAL_CLIENT_SECRET', '')
PAYPAL_BASE = 'https://api-m.paypal.com'  # LIVE only
DB_PATH = os.getenv('SOV_DB', '/var/data/sov_lite.sqlite3' if os.path.isdir('/var/data') else 'sov_lite.sqlite3')
if not APP_SECRET:
    # Do not use this fallback in production; Render must set APP_SECRET.
    APP_SECRET = secrets.token_urlsafe(48)
serializer = URLSafeTimedSerializer(APP_SECRET, salt='sov-lite-auth-v1')

PACKAGES = {10000: 10.00, 30000: 45.00, 100000: 100.00, 300000: 250.00}

def utcnow(): return datetime.now(timezone.utc).isoformat()
def db():
    if 'db' not in g:
        os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
        g.db = sqlite3.connect(DB_PATH, timeout=20)
        g.db.row_factory = sqlite3.Row
    return g.db
@app.teardown_appcontext
def close_db(exc=None):
    conn = g.pop('db', None)
    if conn is not None: conn.close()

def init_db():
    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,display_name TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tapjoy_rewards(transaction_id TEXT PRIMARY KEY,uid TEXT NOT NULL,currency REAL NOT NULL,status TEXT NOT NULL,raw_data TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paypal_orders(order_id TEXT PRIMARY KEY,uid TEXT NOT NULL,views INTEGER NOT NULL,amount_cents INTEGER NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS wallet_links(uid TEXT NOT NULL,address TEXT NOT NULL,network TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(uid,address,network));
        ''')
        db().commit()
init_db()

def get_user_id():
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '): return None
    try:
        data = serializer.loads(auth[7:].strip(), max_age=60*60*24*30)
        return int(data['uid'])
    except (BadSignature, SignatureExpired, ValueError, KeyError, TypeError): return None

def auth_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        uid = get_user_id()
        if not uid: return jsonify(ok=False, error='Please sign in again.'), 401
        row = db().execute('SELECT id,email,display_name FROM users WHERE id=?', (uid,)).fetchone()
        if not row: return jsonify(ok=False, error='Account not found.'), 401
        g.user = row
        return fn(*args, **kwargs)
    return wrapped

def clean_email(v): return str(v or '').strip().lower()[:254]

@app.get('/')
def index(): return send_from_directory('static', 'index.html')
@app.get('/api/health')
def health(): return jsonify(ok=True, service='Scorpio Octavious Vibe Lite', status='online', firebase=False, time=utcnow())
@app.get('/api/config')
def config():
    return jsonify(ok=True, firebase=False, tapjoyConfigured=bool(TAPJOY_WEB_SDK_KEY), tapjoyWebSdkKey=TAPJOY_WEB_SDK_KEY, paypalLiveConfigured=bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET), paypalClientId=PAYPAL_CLIENT_ID, boostPackages=[{'views':v,'amount':a,'currency':'USD'} for v,a in PACKAGES.items()], walletNetworks=['ethereum','polygon'])

@app.post('/api/auth/signup')
def signup():
    data=request.get_json(silent=True) or {}; email=clean_email(data.get('email')); password=str(data.get('password') or ''); name=str(data.get('displayName') or email.split('@')[0] or 'SOV User').strip()[:60]
    if '@' not in email or len(password)<10: return jsonify(ok=False,error='Enter a valid email and a password with at least 10 characters.'),400
    try:
        cur=db().execute('INSERT INTO users(email,password_hash,display_name,created_at) VALUES(?,?,?,?)',(email,generate_password_hash(password),name,utcnow())); db().commit(); uid=cur.lastrowid
    except sqlite3.IntegrityError: return jsonify(ok=False,error='An account with this email already exists.'),409
    token=serializer.dumps({'uid':uid})
    return jsonify(ok=True,token=token,user={'id':uid,'email':email,'displayName':name}),201
@app.post('/api/auth/login')
def login():
    data=request.get_json(silent=True) or {}; email=clean_email(data.get('email')); password=str(data.get('password') or '')
    row=db().execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if not row or not check_password_hash(row['password_hash'],password): return jsonify(ok=False,error='Email or password is incorrect.'),401
    token=serializer.dumps({'uid':row['id']})
    return jsonify(ok=True,token=token,user={'id':row['id'],'email':row['email'],'displayName':row['display_name']})
@app.get('/api/me')
@auth_required
def me(): return jsonify(ok=True,user={'id':g.user['id'],'email':g.user['email'],'displayName':g.user['display_name']})

@app.post('/api/wallet/link')
@auth_required
def link_wallet():
    data=request.get_json(silent=True) or {}; address=str(data.get('address') or '').strip(); network=str(data.get('network') or 'ethereum').strip().lower()
    if not (address.startswith('0x') and len(address)==42): return jsonify(ok=False,error='Enter a valid EVM wallet address.'),400
    if network not in ('ethereum','polygon'): return jsonify(ok=False,error='Unsupported network.'),400
    db().execute('INSERT OR IGNORE INTO wallet_links(uid,address,network,created_at) VALUES(?,?,?,?)',(str(g.user['id']),address,network,utcnow())); db().commit()
    return jsonify(ok=True,address=address,network=network,linked=True)
@app.get('/api/wallet/links')
@auth_required
def wallet_links():
    rows=db().execute('SELECT address,network,created_at FROM wallet_links WHERE uid=? ORDER BY created_at DESC',(str(g.user['id']),)).fetchall()
    return jsonify(ok=True,wallets=[dict(x) for x in rows])
@app.get('/api/rewards')
@auth_required
def rewards():
    rows=db().execute('SELECT transaction_id,currency,status,created_at FROM tapjoy_rewards WHERE uid=? ORDER BY created_at DESC LIMIT 100',(str(g.user['id']),)).fetchall()
    total=db().execute('SELECT COALESCE(SUM(currency),0) AS total FROM tapjoy_rewards WHERE uid=?',(str(g.user['id']),)).fetchone()['total']
    return jsonify(ok=True,totalProviderCurrency=total,rewards=[dict(x) for x in rows])

def paypal_token():
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET: raise RuntimeError('Set PayPal LIVE client ID and secret in Render environment variables.')
    r=requests.post(PAYPAL_BASE+'/v1/oauth2/token',auth=(PAYPAL_CLIENT_ID,PAYPAL_CLIENT_SECRET),data={'grant_type':'client_credentials'},timeout=20); r.raise_for_status(); return r.json()['access_token']
@app.post('/api/paypal/create-order')
@auth_required
def paypal_create():
    data=request.get_json(silent=True) or {}
    try: views=int(data.get('views',0))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid package.'),400
    if views not in PACKAGES: return jsonify(ok=False,error='Choose a listed boost package.'),400
    amount=PACKAGES[views]
    try:
        access=paypal_token()
        r=requests.post(PAYPAL_BASE+'/v2/checkout/orders',headers={'Authorization':'Bearer '+access,'Content-Type':'application/json'},json={'intent':'CAPTURE','purchase_units':[{'reference_id':'SOV-LITE-BOOST','custom_id':str(g.user['id']),'description':f'SOV promotion package - {views:,} views','amount':{'currency_code':'USD','value':f'{amount:.2f}'}}],'application_context':{'brand_name':'Scorpio Octavious Vibe','user_action':'PAY_NOW','return_url':request.host_url+'?paypal=return','cancel_url':request.host_url+'?paypal=cancel'}},timeout=25)
        r.raise_for_status(); result=r.json(); order_id=result['id']
        db().execute('INSERT INTO paypal_orders(order_id,uid,views,amount_cents,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(order_id,str(g.user['id']),views,round(amount*100),'CREATED',utcnow(),utcnow())); db().commit()
        approve=next((x['href'] for x in result.get('links',[]) if x.get('rel')=='approve'),None)
        if not approve: return jsonify(ok=False,error='PayPal did not return an approval URL.'),502
        return jsonify(ok=True,orderId=order_id,approvalUrl=approve)
    except Exception as e: return jsonify(ok=False,error='Could not create PayPal LIVE order. Check Render credentials.',details=str(e)[:250]),503
@app.post('/api/paypal/capture-order/<order_id>')
@auth_required
def paypal_capture(order_id):
    row=db().execute('SELECT * FROM paypal_orders WHERE order_id=? AND uid=?',(order_id,str(g.user['id']))).fetchone()
    if not row: return jsonify(ok=False,error='Order not found.'),404
    if row['status']=='COMPLETED': return jsonify(ok=True,status='COMPLETED',alreadyCaptured=True)
    try:
        access=paypal_token(); r=requests.post(PAYPAL_BASE+f'/v2/checkout/orders/{order_id}/capture',headers={'Authorization':'Bearer '+access,'Content-Type':'application/json'},timeout=25); r.raise_for_status(); data=r.json(); status=data.get('status','UNKNOWN')
        db().execute('UPDATE paypal_orders SET status=?,updated_at=? WHERE order_id=?',(status,utcnow(),order_id)); db().commit()
        return jsonify(ok=status=='COMPLETED',status=status,orderId=order_id,views=row['views'])
    except Exception as e: return jsonify(ok=False,error='PayPal capture failed.',details=str(e)[:250]),503

@app.route('/api/tapjoy/callback',methods=['GET','POST'])
def tapjoy_callback():
    values=request.values; tx=(values.get('id') or values.get('transaction_id') or '').strip(); uid=(values.get('snuid') or values.get('publisher_user_id') or '').strip(); currency=(values.get('currency') or values.get('amount') or '0').strip(); verifier=(values.get('verifier') or values.get('signature') or '').strip()
    if not TAPJOY_SECRET: return jsonify(ok=False,error='Tapjoy callback secret is not configured.'),503
    if not tx or not uid or not verifier: return jsonify(ok=False,error='Missing required Tapjoy callback fields.'),400
    try: amount=float(currency)
    except ValueError: return jsonify(ok=False,error='Invalid currency amount.'),400
    if amount<0: return jsonify(ok=False,error='Invalid currency amount.'),400
    expected=hashlib.md5(f'{tx}:{uid}:{currency}:{TAPJOY_SECRET}'.encode()).hexdigest()
    if not hmac.compare_digest(expected.lower(),verifier.lower()): return jsonify(ok=False,error='Invalid Tapjoy verifier.'),403
    try:
        db().execute('INSERT INTO tapjoy_rewards(transaction_id,uid,currency,status,raw_data,created_at) VALUES(?,?,?,?,?,?)',(tx,uid,amount,'VERIFIED',json.dumps(dict(values)),utcnow())); db().commit()
        return jsonify(ok=True,success=True,duplicate=False,transactionId=tx)
    except sqlite3.IntegrityError: return jsonify(ok=True,success=True,duplicate=True,transactionId=tx)

@app.get('/api/admin/summary')
def admin_summary():
    # Intentionally omitted from the lite public client. Add private admin auth before exposing it.
    return jsonify(ok=False,error='Admin API is not enabled in SOV Lite.'),403

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
