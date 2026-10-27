import os
import re
import json
import uuid
import sqlite3
import hashlib
import hmac
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta, date
from functools import wraps

import jwt
import requests
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

# Optional Google Sign-In support is retained for compatibility with older builds.
try:
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
except Exception:
    id_token = None
    google_requests = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv('DATABASE_PATH', os.path.join(APP_DIR, 'sov.sqlite3'))
JWT_SECRET = os.getenv('JWT_SECRET', '')
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', '').strip().lower()

MIN_PAYOUT_USD = 90.0
WITHDRAWAL_FEE_PERCENT = 50.0

PAYPAL_CLIENT_ID = os.getenv('PAYPAL_CLIENT_ID', '')
PAYPAL_CLIENT_SECRET = os.getenv('PAYPAL_CLIENT_SECRET', '')
PAYPAL_MODE = os.getenv('PAYPAL_MODE', 'sandbox').strip().lower()
PAYPAL_BASE_URL = 'https://api-m.paypal.com' if PAYPAL_MODE == 'live' else 'https://api-m.sandbox.paypal.com'
PAYPAL_CURRENCY = os.getenv('PAYPAL_CURRENCY', 'USD').upper()
FRONTEND_ORIGIN = os.getenv('FRONTEND_ORIGIN', '*')

BOOST_PACKAGES = {
    'boost_10': {'id': 'boost_10', 'price_usd': 10, 'views': 10000, 'title': '10,000 views'},
    'boost_20': {'id': 'boost_20', 'price_usd': 20, 'views': 25000, 'title': '25,000 views'},
    'boost_35': {'id': 'boost_35', 'price_usd': 35, 'views': 50000, 'title': '50,000 views'},
    'boost_60': {'id': 'boost_60', 'price_usd': 60, 'views': 100000, 'title': '100,000 views'},
    'boost_250': {'id': 'boost_250', 'price_usd': 250, 'views': 500000, 'title': '500,000 views'},
}

app = Flask(__name__)
allowed_origins = os.getenv('CORS_ORIGINS', '*')
CORS(app, resources={r'/api/*': {'origins': '*' if allowed_origins == '*' else [x.strip() for x in allowed_origins.split(',') if x.strip()]}}, supports_credentials=False)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def table_columns(db, table):
    return {row['name'] for row in db.execute(f'PRAGMA table_info({table})').fetchall()}


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        google_sub TEXT UNIQUE NOT NULL,
        email TEXT NOT NULL,
        name TEXT NOT NULL,
        picture TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ledger (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        amount_usd REAL NOT NULL,
        entry_type TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        reference TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS withdrawals (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        gross_amount_usd REAL NOT NULL,
        fee_usd REAL NOT NULL,
        net_amount_usd REAL NOT NULL,
        method TEXT NOT NULL,
        destination TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending_review',
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS offers (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        provider_transaction_id TEXT NOT NULL,
        amount_usd REAL NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS campaigns (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        package_id TEXT NOT NULL,
        amount_usd REAL NOT NULL,
        views INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending_payment',
        paypal_order_id TEXT,
        paypal_capture_id TEXT,
        payment_provider TEXT NOT NULL DEFAULT 'paypal',
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    ''')
    # Safe additive migrations: existing user IDs, wallet ledger, withdrawals and campaigns remain intact.
    cols = table_columns(db, 'users')
    for name, definition in [
        ('password_hash', 'TEXT'),
        ('first_name', 'TEXT'),
        ('last_name', 'TEXT'),
        ('date_of_birth', 'TEXT'),
    ]:
        if name not in cols:
            db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
    ccols = table_columns(db, 'campaigns')
    for name, definition in [
        ('paypal_order_id', 'TEXT'),
        ('paypal_capture_id', 'TEXT'),
        ('payment_provider', "TEXT NOT NULL DEFAULT 'paypal'"),
    ]:
        if name not in ccols:
            db.execute(f'ALTER TABLE campaigns ADD COLUMN {name} {definition}')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_offers_provider_tx ON offers(provider, provider_transaction_id)')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_paypal_order ON campaigns(paypal_order_id) WHERE paypal_order_id IS NOT NULL')
    db.commit()
    db.close()


def json_error(message, status=400, **extra):
    return jsonify({'ok': False, 'error': message, **extra}), status


def user_payload(user):
    first = user['first_name'] if 'first_name' in user.keys() and user['first_name'] else ''
    last = user['last_name'] if 'last_name' in user.keys() and user['last_name'] else ''
    return {
        'id': user['id'], 'email': user['email'], 'name': user['name'],
        'picture': user['picture'] or '', 'first_name': first, 'last_name': last,
        'date_of_birth': user['date_of_birth'] if 'date_of_birth' in user.keys() else None
    }


def issue_token(user):
    if not JWT_SECRET:
        raise RuntimeError('JWT_SECRET is not configured on the server.')
    now = datetime.now(timezone.utc)
    token = jwt.encode({'sub': user['id'], 'email': user['email'], 'iat': now, 'exp': now + timedelta(hours=12)}, JWT_SECRET, algorithm='HS256')
    return {'access_token': token, 'token_type': 'Bearer', 'expires_in': 43200, 'user': user_payload(user)}


def current_user():
    header = request.headers.get('Authorization', '')
    if not header.lower().startswith('bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    if not token or not JWT_SECRET:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        uid = payload.get('sub')
        if not uid:
            return None
        return get_db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    except Exception:
        return None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return json_error('Sign in first. Your token may be missing, expired, or invalid.', 401)
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not ADMIN_EMAIL or g.user['email'].lower() != ADMIN_EMAIL:
            return json_error('Admin access is not configured for this account.', 403)
        return fn(*args, **kwargs)
    return wrapper


def parse_amount(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0:
            return None
        return amount.quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return None


def valid_email(email):
    return bool(re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email or ''))


def auth_response(user):
    try:
        return jsonify({'ok': True, **issue_token(user)})
    except RuntimeError as exc:
        return json_error(str(exc), 503)


@app.get('/api/health')
def health():
    return jsonify({'ok': True, 'service': 'Scorpio Octavious Vibe backend', 'auth_configured': bool(JWT_SECRET), 'google_signin_configured': bool(GOOGLE_CLIENT_ID), 'paypal_configured': bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET), 'paypal_mode': PAYPAL_MODE})


@app.post('/api/auth/register')
def register():
    data = request.get_json(silent=True) or {}
    first = str(data.get('first_name', data.get('firstName', ''))).strip()
    last = str(data.get('last_name', data.get('lastName', ''))).strip()
    email = str(data.get('email', '')).strip().lower()
    dob = str(data.get('date_of_birth', data.get('dateOfBirth', ''))).strip()
    password = data.get('password', '')
    confirm = data.get('confirm_password', data.get('confirmPassword', ''))
    if not first or not last:
        return json_error('First name and last name are required.')
    if len(first) > 80 or len(last) > 80:
        return json_error('Names must be 80 characters or fewer.')
    if not valid_email(email):
        return json_error('Enter a valid email address.')
    try:
        born = date.fromisoformat(dob)
        if born >= date.today() or born < date(1900, 1, 1):
            return json_error('Enter a valid date of birth.')
    except (ValueError, TypeError):
        return json_error('Date of birth must use YYYY-MM-DD format.')
    if not isinstance(password, str) or len(password) < 8:
        return json_error('Password must be at least 8 characters long.')
    if password != confirm:
        return json_error('Password and confirm password do not match.')
    db = get_db()
    if db.execute('SELECT id FROM users WHERE lower(email)=?', (email,)).fetchone():
        return json_error('An account with this email already exists. Please sign in instead.', 409)
    uid = str(uuid.uuid4())
    name = f'{first} {last}'
    # The existing schema requires google_sub. This reserved marker allows password accounts
    # without rebuilding the users table or disturbing existing wallet-related foreign keys.
    marker = 'email:' + email
    try:
        db.execute('INSERT INTO users (id, google_sub, email, name, picture, created_at, password_hash, first_name, last_name, date_of_birth) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (uid, marker, email, name, '', utc_now(), generate_password_hash(password), first, last, born.isoformat()))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return json_error('An account with this email already exists.', 409)
    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    return auth_response(user), 201


@app.post('/api/auth/login')
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get('email', '')).strip().lower()
    password = data.get('password', '')
    if not email or not isinstance(password, str) or not password:
        return json_error('Email and password are required.')
    user = get_db().execute('SELECT * FROM users WHERE lower(email)=?', (email,)).fetchone()
    if user is None or not user['password_hash'] or not check_password_hash(user['password_hash'], password):
        return json_error('Invalid email or password.', 401)
    return auth_response(user)


# Retained for older deployed clients. New email/password screens should use /api/auth/register and /api/auth/login.
@app.post('/api/auth/google')
def google_login():
    if not GOOGLE_CLIENT_ID or id_token is None or google_requests is None:
        return json_error('Google Sign-In is not configured on this server.', 503)
    data = request.get_json(silent=True) or {}
    credential = data.get('credential') or data.get('id_token') or data.get('token')
    if not credential:
        return json_error('Google credential is required.')
    try:
        info = id_token.verify_oauth2_token(credential, google_requests.Request(), GOOGLE_CLIENT_ID)
        sub = info.get('sub')
        email = str(info.get('email', '')).strip().lower()
        if not sub or not email or not info.get('email_verified', False):
            return json_error('Google account email is not verified.', 401)
    except Exception:
        return json_error('Google credential is invalid or expired.', 401)
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE google_sub=?', (sub,)).fetchone()
    if user is None:
        # Do not silently link a Google account to a password account by email.
        if db.execute('SELECT id FROM users WHERE lower(email)=?', (email,)).fetchone():
            return json_error('This email already has an account. Sign in using its existing method.', 409)
        uid = str(uuid.uuid4())
        name = str(info.get('name') or email.split('@')[0])[:160]
        picture = str(info.get('picture') or '')[:1000]
        db.execute('INSERT INTO users (id, google_sub, email, name, picture, created_at, first_name, last_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                   (uid, sub, email, name, picture, utc_now(), str(info.get('given_name') or ''), str(info.get('family_name') or '')))
        db.commit()
        user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    else:
        db.execute('UPDATE users SET email=?, name=?, picture=? WHERE id=?', (email, str(info.get('name') or user['name']), str(info.get('picture') or user['picture']), user['id']))
        db.commit()
        user = db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
    try:
        return jsonify({'ok': True, **issue_token(user)})
    except RuntimeError as exc:
        return json_error(str(exc), 503)


@app.get('/api/me')
@login_required
def me():
    return jsonify({'ok': True, 'user': user_payload(g.user)})


@app.get('/api/wallet')
@login_required
def wallet():
    db = get_db()
    rows = db.execute('SELECT COALESCE(SUM(amount_usd),0) AS balance FROM ledger WHERE user_id=?', (g.user['id'],)).fetchone()
    recent = db.execute('SELECT id, amount_usd, entry_type, description, reference, created_at FROM ledger WHERE user_id=? ORDER BY created_at DESC LIMIT 50', (g.user['id'],)).fetchall()
    return jsonify({'ok': True, 'balance_usd': round(float(rows['balance'] or 0), 2), 'currency': 'USD', 'transactions': [dict(r) for r in recent]})


@app.get('/api/wallet/withdrawals')
@login_required
def withdrawal_history():
    rows = get_db().execute('SELECT id, gross_amount_usd, fee_usd, net_amount_usd, method, destination, status, created_at FROM withdrawals WHERE user_id=? ORDER BY created_at DESC LIMIT 100', (g.user['id'],)).fetchall()
    return jsonify({'ok': True, 'withdrawals': [dict(r) for r in rows], 'minimum_payout_usd': MIN_PAYOUT_USD, 'fee_percent': WITHDRAWAL_FEE_PERCENT})


@app.post('/api/withdrawals')
@login_required
def create_withdrawal():
    data = request.get_json(silent=True) or {}
    amount = parse_amount(data.get('amount_usd', data.get('amount')))
    method = str(data.get('method', '')).strip().lower()
    destination = str(data.get('destination', data.get('wallet_address', data.get('paypal_email', '')))).strip()
    if amount is None:
        return json_error('Enter a valid withdrawal amount.')
    if float(amount) < MIN_PAYOUT_USD:
        return json_error(f'Minimum payout is ${MIN_PAYOUT_USD:.2f}.')
    if method not in ('paypal', 'crypto', 'crypto wallet'):
        return json_error('Withdrawal method must be PayPal or Crypto wallet.')
    if not destination or len(destination) > 500:
        return json_error('A valid payout destination is required.')
    db = get_db()
    balance_row = db.execute('SELECT COALESCE(SUM(amount_usd),0) AS balance FROM ledger WHERE user_id=?', (g.user['id'],)).fetchone()
    balance = Decimal(str(balance_row['balance'] or 0)).quantize(Decimal('0.01'))
    if amount > balance:
        return json_error('Insufficient available wallet balance.', 409, balance_usd=float(balance))
    fee = (amount * Decimal(str(WITHDRAWAL_FEE_PERCENT)) / Decimal('100')).quantize(Decimal('0.01'))
    net = amount - fee
    wid = str(uuid.uuid4())
    db.execute('INSERT INTO withdrawals (id, user_id, gross_amount_usd, fee_usd, net_amount_usd, method, destination, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
               (wid, g.user['id'], float(amount), float(fee), float(net), 'crypto' if method.startswith('crypto') else 'paypal', destination, 'pending_review', utc_now()))
    # Reserve the gross amount in the ledger so a user cannot submit repeated requests against it.
    db.execute('INSERT INTO ledger (id, user_id, amount_usd, entry_type, description, reference, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
               (str(uuid.uuid4()), g.user['id'], -float(amount), 'withdrawal_hold', f'Withdrawal request {wid} (pending review)', wid, utc_now()))
    db.commit()
    return jsonify({'ok': True, 'withdrawal': {'id': wid, 'gross_amount_usd': float(amount), 'fee_percent': WITHDRAWAL_FEE_PERCENT, 'fee_usd': float(fee), 'net_amount_usd': float(net), 'status': 'pending_review'}, 'message': 'Request saved for review. This endpoint does not send a payout automatically.'}), 201


@app.get('/api/boost/packages')
def boost_packages():
    return jsonify({'ok': True, 'packages': list(BOOST_PACKAGES.values()), 'currency': 'USD'})


def paypal_ready():
    return bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)


def paypal_access_token():
    if not paypal_ready():
        raise RuntimeError('PayPal is not configured. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.')
    response = requests.post(PAYPAL_BASE_URL + '/v1/oauth2/token', auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET), data={'grant_type': 'client_credentials'}, timeout=20)
    if response.status_code >= 400:
        raise RuntimeError('PayPal authentication failed.')
    return response.json()['access_token']


def paypal_api(method, path, body=None):
    token = paypal_access_token()
    response = requests.request(method, PAYPAL_BASE_URL + path, headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, json=body, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        raise RuntimeError(payload.get('message') or 'PayPal request failed.')
    return payload


@app.post('/api/boost/paypal/create-order')
@login_required
def create_boost_order():
    data = request.get_json(silent=True) or {}
    package_id = str(data.get('package_id', data.get('package', '')))
    package = BOOST_PACKAGES.get(package_id)
    if not package:
        return json_error('Choose a valid boost package.')
    campaign_id = str(uuid.uuid4())
    try:
        order = paypal_api('POST', '/v2/checkout/orders', {'intent': 'CAPTURE', 'purchase_units': [{'reference_id': campaign_id, 'custom_id': campaign_id, 'description': 'SOV Boost: ' + package['title'], 'amount': {'currency_code': PAYPAL_CURRENCY, 'value': f"{package['price_usd']:.2f}"}}]})
    except RuntimeError as exc:
        return json_error(str(exc), 503)
    db = get_db()
    db.execute('INSERT INTO campaigns (id, user_id, package_id, amount_usd, views, status, paypal_order_id, payment_provider, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
               (campaign_id, g.user['id'], package_id, package['price_usd'], package['views'], 'pending_payment', order.get('id'), 'paypal', utc_now()))
    db.commit()
    approval = next((x.get('href') for x in order.get('links', []) if x.get('rel') == 'approve'), None)
    return jsonify({'ok': True, 'campaign_id': campaign_id, 'order_id': order.get('id'), 'status': order.get('status'), 'approval_url': approval, 'links': order.get('links', [])}), 201


@app.post('/api/boost/paypal/capture')
@login_required
def capture_boost_order():
    data = request.get_json(silent=True) or {}
    order_id = str(data.get('order_id', '')).strip()
    if not order_id:
        return json_error('PayPal order_id is required.')
    db = get_db()
    campaign = db.execute('SELECT * FROM campaigns WHERE paypal_order_id=? AND user_id=?', (order_id, g.user['id'])).fetchone()
    if campaign is None:
        return json_error('Boost campaign was not found for this account.', 404)
    if campaign['status'] == 'paid':
        return jsonify({'ok': True, 'campaign_id': campaign['id'], 'status': 'paid', 'message': 'This order was already captured.'})
    try:
        capture = paypal_api('POST', f'/v2/checkout/orders/{order_id}/capture', {})
    except RuntimeError as exc:
        # PayPal may report an already-captured order; query its authoritative status.
        try:
            capture = paypal_api('GET', f'/v2/checkout/orders/{order_id}')
        except RuntimeError:
            return json_error(str(exc), 502)
    if capture.get('status') != 'COMPLETED':
        return json_error('PayPal has not confirmed a completed payment.', 409, paypal_status=capture.get('status'))
    captures = []
    for unit in capture.get('purchase_units', []):
        captures.extend(unit.get('payments', {}).get('captures', []))
    completed = next((c for c in captures if c.get('status') == 'COMPLETED'), None)
    if not completed:
        return json_error('No completed capture was found in the PayPal response.', 409)
    amount_info = completed.get('amount', {})
    try:
        paid_amount = Decimal(str(amount_info.get('value'))).quantize(Decimal('0.01'))
    except Exception:
        return json_error('PayPal returned an invalid capture amount.', 502)
    expected = Decimal(str(campaign['amount_usd'])).quantize(Decimal('0.01'))
    if amount_info.get('currency_code') != PAYPAL_CURRENCY or paid_amount != expected:
        return json_error('PayPal capture amount or currency does not match this campaign.', 409)
    db.execute("UPDATE campaigns SET status='paid', paypal_capture_id=? WHERE id=? AND status!='paid'", (completed.get('id'), campaign['id']))
    db.commit()
    return jsonify({'ok': True, 'campaign_id': campaign['id'], 'capture_id': completed.get('id'), 'status': 'paid', 'views': campaign['views'], 'message': 'Payment confirmed. Campaign is recorded; view delivery/fulfilment must be implemented separately.'})


@app.get('/api/boost/campaigns')
@login_required
def list_boost_campaigns():
    rows = get_db().execute('SELECT id, package_id, amount_usd, views, status, payment_provider, created_at FROM campaigns WHERE user_id=? ORDER BY created_at DESC LIMIT 100', (g.user['id'],)).fetchall()
    return jsonify({'ok': True, 'campaigns': [dict(r) for r in rows]})


@app.post('/api/boost/crypto/create-intent')
@login_required
def crypto_boost_disabled():
    return json_error('Crypto payments are not connected to a verified payment provider yet. No payment was taken.', 503)


@app.get('/api/earn/summary')
@login_required
def earn_summary():
    db = get_db()
    total = db.execute("SELECT COALESCE(SUM(amount_usd),0) AS total FROM ledger WHERE user_id=? AND amount_usd>0", (g.user['id'],)).fetchone()['total']
    return jsonify({'ok': True, 'credited_earnings_usd': round(float(total or 0), 2), 'minimum_payout_usd': MIN_PAYOUT_USD, 'withdrawal_fee_percent': WITHDRAWAL_FEE_PERCENT, 'payout_schedule': 'monthly'})


@app.post('/api/tapjoy/callback')
def tapjoy_callback():
    # Fail closed until the exact Tapjoy callback signature and validation contract are configured.
    secret = os.getenv('TAPJOY_SECRET', '')
    signature = request.headers.get('X-Tapjoy-Signature', '')
    if not secret or not signature:
        return json_error('Tapjoy callback verification is not configured; no reward was credited.', 503)
    raw = request.get_data(cache=True)
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return json_error('Invalid Tapjoy callback signature.', 401)
    # Do not guess the provider's transaction/user/amount field mapping.
    return json_error('Signature verified, but Tapjoy field mapping must be configured before crediting rewards.', 501)


@app.post('/api/admin/ledger/credit')
@admin_required
def admin_credit():
    data = request.get_json(silent=True) or {}
    target_email = str(data.get('email', '')).strip().lower()
    amount = parse_amount(data.get('amount_usd', data.get('amount')))
    description = str(data.get('description', 'Admin credit')).strip()[:300]
    if not valid_email(target_email) or amount is None:
        return json_error('A valid user email and positive amount are required.')
    db = get_db()
    target = db.execute('SELECT id FROM users WHERE lower(email)=?', (target_email,)).fetchone()
    if not target:
        return json_error('User not found.', 404)
    db.execute('INSERT INTO ledger (id, user_id, amount_usd, entry_type, description, reference, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
               (str(uuid.uuid4()), target['id'], float(amount), 'admin_credit', description, 'admin:' + g.user['id'], utc_now()))
    db.commit()
    return jsonify({'ok': True, 'credited_email': target_email, 'amount_usd': float(amount)})


@app.get('/api/admin/summary')
@admin_required
def admin_summary():
    db = get_db()
    users = db.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
    pending = db.execute("SELECT COUNT(*) AS n FROM withdrawals WHERE status='pending_review'").fetchone()['n']
    campaigns = db.execute("SELECT COUNT(*) AS n FROM campaigns").fetchone()['n']
    recent_users = db.execute('SELECT id,email,name,created_at FROM users ORDER BY created_at DESC LIMIT 100').fetchall()
    return jsonify({'ok': True, 'user_count': users, 'pending_withdrawals': pending, 'campaign_count': campaigns, 'users': [dict(r) for r in recent_users]})


@app.errorhandler(500)
def internal_error(_error):
    return jsonify({'ok': False, 'error': 'Internal server error.'}), 500


init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')))
