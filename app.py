import os, sqlite3, hashlib, hmac, secrets, smtplib, uuid
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from functools import wraps
from flask import Flask, request, jsonify, render_template, g, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

app=Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)
app.config['SECRET_KEY']=os.getenv('JWT_SECRET') or secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH']=5*1024*1024
UPLOAD_DIR=os.getenv('SOV_UPLOAD_DIR', '/var/data/uploads' if os.path.isdir('/var/data') else os.path.join(app.static_folder,'uploads'))
os.makedirs(UPLOAD_DIR,exist_ok=True)
DB=os.getenv('SOV_DB','/var/data/sov.db' if os.path.isdir('/var/data') else 'sov.db')
ADMIN_EMAIL=os.getenv('ADMIN_EMAIL','kinxintruder@gmail.com').lower().strip()
WITHDRAWAL_NOTIFY_EMAIL=os.getenv('WITHDRAWAL_NOTIFY_EMAIL','kinxintruder@gmail.com').strip()
SMTP_HOST=os.getenv('SMTP_HOST','').strip()
SMTP_PORT=int(os.getenv('SMTP_PORT','587'))
SMTP_USERNAME=os.getenv('SMTP_USERNAME','').strip()
SMTP_PASSWORD=os.getenv('SMTP_PASSWORD','')
SMTP_FROM=os.getenv('SMTP_FROM',SMTP_USERNAME).strip()
WITHDRAWAL_FEE_PERCENT=10.0
TAPJOY_SECRET=os.getenv('TAPJOY_SECRET','')
TAPJOY_WEB_SDK_KEY=os.getenv('TAPJOY_WEB_SDK_KEY','')
SOV_FEE_WALLET=os.getenv('SOV_FEE_WALLET','')
PAYPAL_CLIENT_ID=os.getenv('PAYPAL_CLIENT_ID','')
serializer=URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='sov-auth-v1')

def db():
    if 'db' not in g:
        g.db=sqlite3.connect(DB); g.db.row_factory=sqlite3.Row
    return g.db
@app.teardown_appcontext
def close_db(exc):
    c=g.pop('db',None)
    if c: c.close()
def init_db():
    os.makedirs(os.path.dirname(DB),exist_ok=True) if os.path.dirname(DB) else None
    with sqlite3.connect(DB) as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password TEXT NOT NULL,created_at TEXT NOT NULL,nickname TEXT NOT NULL DEFAULT '',profile_pic TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY AUTOINCREMENT,uid TEXT NOT NULL,text TEXT,media_url TEXT,media_type TEXT,created_at TEXT NOT NULL,likes INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS likes(post_id INTEGER NOT NULL,uid TEXT NOT NULL,PRIMARY KEY(post_id,uid));
        CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY AUTOINCREMENT,post_id INTEGER NOT NULL,uid TEXT NOT NULL,text TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS follows(uid TEXT NOT NULL,target TEXT NOT NULL,PRIMARY KEY(uid,target));
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,sender TEXT NOT NULL,recipient TEXT NOT NULL,text TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tapjoy_rewards(txid TEXT PRIMARY KEY,uid TEXT NOT NULL,currency REAL NOT NULL,created_at TEXT NOT NULL,raw TEXT);
        CREATE TABLE IF NOT EXISTS wallet_sends(id INTEGER PRIMARY KEY AUTOINCREMENT,uid TEXT,sender TEXT,recipient TEXT,amount TEXT,fee TEXT,recipient_tx TEXT,fee_tx TEXT,network TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS boosts(id INTEGER PRIMARY KEY AUTOINCREMENT,uid TEXT,views INTEGER,amount REAL,status TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS withdrawal_requests(id INTEGER PRIMARY KEY AUTOINCREMENT,uid TEXT NOT NULL,amount REAL NOT NULL,fee REAL NOT NULL,net_amount REAL NOT NULL,currency TEXT NOT NULL,payout_method TEXT NOT NULL,payout_details TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',admin_note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,uid TEXT,text TEXT,created_at TEXT,seen INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS game_rewards(id INTEGER PRIMARY KEY AUTOINCREMENT,uid TEXT NOT NULL,game_name TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'VERIFIED',source_ref TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
        ''')
        cols={r[1] for r in c.execute('PRAGMA table_info(users)').fetchall()}
        if 'nickname' not in cols: c.execute("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
        if 'profile_pic' not in cols: c.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT NOT NULL DEFAULT ''")
        c.execute("UPDATE users SET nickname=name WHERE nickname='' OR nickname IS NULL")
init_db()
def timestamp(): return datetime.now(timezone.utc).isoformat()
def auth_required(fn):
    @wraps(fn)
    def wrap(*args,**kwargs):
        token=(request.headers.get('Authorization') or '').removeprefix('Bearer ').strip()
        if not token: return jsonify(ok=False,error='Please log in'),401
        try: data=serializer.loads(token,max_age=60*60*24*30)
        except (BadSignature,SignatureExpired): return jsonify(ok=False,error='Session expired. Log in again.'),401
        user=db().execute('SELECT id,email,name,nickname,profile_pic FROM users WHERE id=?',(data.get('uid'),)).fetchone()
        if not user: return jsonify(ok=False,error='Account not found'),401
        g.user=dict(user)
        return fn(*args,**kwargs)
    return wrap
def uid(): return g.user['id']
def body(): return request.get_json(silent=True) or {}

def send_withdrawal_email(subject, text_body):
    """Send an admin notification when SMTP credentials are configured."""
    if not (SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM and WITHDRAWAL_NOTIFY_EMAIL):
        app.logger.warning('Withdrawal request saved but SMTP is not configured; admin email was not sent.')
        return False
    msg=EmailMessage(); msg['Subject']=subject; msg['From']=SMTP_FROM; msg['To']=WITHDRAWAL_NOTIFY_EMAIL; msg.set_content(text_body)
    try:
        with smtplib.SMTP(SMTP_HOST,SMTP_PORT,timeout=15) as server:
            server.starttls(); server.login(SMTP_USERNAME,SMTP_PASSWORD); server.send_message(msg)
        return True
    except Exception:
        app.logger.exception('Could not send withdrawal notification email')
        return False

@app.get('/')
def home(): return render_template('index.html')
@app.get('/api/health')
def health(): return jsonify(ok=True,service='Scorpio Octavious Vibe Lite',status='online',firebase=False)
@app.get('/api/config')
def config(): return jsonify(ok=True,firebase=False,tapjoyWebSdkKey=TAPJOY_WEB_SDK_KEY,sovFeeWallet=SOV_FEE_WALLET,sovSendFeePercent=10,paypalLiveConfigured=bool(PAYPAL_CLIENT_ID),network='ethereum-sepolia')
@app.post('/api/auth/register')
def register():
    d=body(); email=str(d.get('email','')).lower().strip(); name=str(d.get('name','')).strip(); password=str(d.get('password',''))
    if '@' not in email or len(password)<8 or not name: return jsonify(ok=False,error='Enter a name, valid email, and password with at least 8 characters.'),400
    ident=secrets.token_urlsafe(16)
    try: db().execute('INSERT INTO users(id,email,name,password,created_at,nickname,profile_pic) VALUES(?,?,?,?,?,?,?)',(ident,email,name,generate_password_hash(password),timestamp(),name,'')); db().commit()
    except sqlite3.IntegrityError: return jsonify(ok=False,error='That email already has an account.'),409
    return jsonify(ok=True,token=serializer.dumps({'uid':ident}),user={'id':ident,'uid':ident,'email':email,'name':name,'nickname':name,'profile_pic':''})
@app.post('/api/auth/login')
def login():
    d=body(); email=str(d.get('email','')).lower().strip(); row=db().execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if not row or not check_password_hash(row['password'],str(d.get('password',''))): return jsonify(ok=False,error='Email or password is incorrect.'),401
    return jsonify(ok=True,token=serializer.dumps({'uid':row['id']}),user={'id':row['id'],'uid':row['id'],'email':row['email'],'name':row['name'],'nickname':row['nickname'] or row['name'],'profile_pic':row['profile_pic'] or ''})
@app.get('/api/me')
@auth_required
def me(): return jsonify(ok=True,user=g.user)
@app.put('/api/profile')
@auth_required
def profile_update():
    d=body(); nickname=str(d.get('nickname',d.get('name',''))).strip()
    if not nickname or len(nickname)>40: return jsonify(ok=False,error='Nickname must be 1–40 characters.'),400
    db().execute('UPDATE users SET nickname=? WHERE id=?',(nickname,uid())); db().commit()
    return jsonify(ok=True,nickname=nickname,name=g.user.get('name') or nickname)

@app.get('/media/profile/<path:filename>')
def serve_profile_photo(filename):
    # Only files inside the dedicated upload directory are served.
    return send_from_directory(UPLOAD_DIR, filename, conditional=True)

@app.post('/api/profile/photo')
@auth_required
def profile_photo_upload():
    f=request.files.get('profilePic')
    if not f or not f.filename: return jsonify(ok=False,error='Choose a profile picture first.'),400
    if f.mimetype not in ('image/jpeg','image/png','image/webp','image/gif'): return jsonify(ok=False,error='Use a JPG, PNG, WEBP, or GIF image.'),400
    raw=f.read(5*1024*1024+1)
    if not raw or len(raw)>5*1024*1024: return jsonify(ok=False,error='Image must be under 5 MB.'),400
    ext={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/gif':'.gif'}[f.mimetype]
    filename=secrets.token_hex(16)+ext
    with open(os.path.join(UPLOAD_DIR,filename),'wb') as out: out.write(raw)
    url='/media/profile/'+filename
    old=db().execute('SELECT profile_pic FROM users WHERE id=?',(uid(),)).fetchone()
    db().execute('UPDATE users SET profile_pic=? WHERE id=?',(url,uid())); db().commit()
    if old and old['profile_pic'] and (old['profile_pic'].startswith('/media/profile/') or old['profile_pic'].startswith('/static/uploads/')):
        try: os.remove(os.path.join(UPLOAD_DIR,os.path.basename(old['profile_pic'])))
        except OSError: pass
    return jsonify(ok=True,profilePic=url)

@app.get('/api/users/search')
@auth_required
def user_search():
    q='%'+str(request.args.get('q','')).strip()+'%'; rows=db().execute('SELECT id,name,nickname,profile_pic,email FROM users WHERE id<>? AND (name LIKE ? OR nickname LIKE ? OR email LIKE ?) LIMIT 50',(uid(),q,q,q)).fetchall(); return jsonify(ok=True,users=[dict(x) for x in rows])
@app.post('/api/follow/<target>')
@auth_required
def follow(target):
    if target==uid(): return jsonify(ok=False,error='You cannot follow yourself'),400
    db().execute('INSERT OR IGNORE INTO follows VALUES(?,?)',(uid(),target)); db().commit(); return jsonify(ok=True,following=True)
@app.delete('/api/follow/<target>')
@auth_required
def unfollow(target): db().execute('DELETE FROM follows WHERE uid=? AND target=?',(uid(),target)); db().commit(); return jsonify(ok=True,following=False)
@app.get('/api/following')
@auth_required
def following():
    rows=db().execute('SELECT u.id,u.name,u.nickname,u.profile_pic,u.email FROM follows f JOIN users u ON u.id=f.target WHERE f.uid=?',(uid(),)).fetchall(); return jsonify(ok=True,users=[dict(x) for x in rows])
@app.get('/api/posts')
@auth_required
def posts():
    rows=db().execute("SELECT p.*,COALESCE(NULLIF(u.nickname,''),u.name) AS author,u.profile_pic AS author_pic FROM posts p JOIN users u ON u.id=p.uid ORDER BY p.id DESC LIMIT 100").fetchall(); return jsonify(ok=True,posts=[dict(x) for x in rows])
@app.post('/api/posts')
@auth_required
def create_post():
    d=body(); text=str(d.get('text','')).strip(); media=str(d.get('mediaUrl','')).strip(); typ=str(d.get('mediaType','')).strip()
    if not text and not media:return jsonify(ok=False,error='Write something or add a media URL'),400
    cur=db().execute('INSERT INTO posts(uid,text,media_url,media_type,created_at) VALUES(?,?,?,?,?)',(uid(),text,media,typ,timestamp())); db().commit(); return jsonify(ok=True,postId=cur.lastrowid),201
@app.post('/api/posts/<int:pid>/like')
@auth_required
def like(pid):
    c=db(); exists=c.execute('SELECT 1 FROM likes WHERE post_id=? AND uid=?',(pid,uid())).fetchone()
    if exists: c.execute('DELETE FROM likes WHERE post_id=? AND uid=?',(pid,uid())); c.execute('UPDATE posts SET likes=MAX(0,likes-1) WHERE id=?',(pid,)); liked=False
    else: c.execute('INSERT OR IGNORE INTO likes VALUES(?,?)',(pid,uid())); c.execute('UPDATE posts SET likes=likes+1 WHERE id=?',(pid,)); liked=True
    c.commit(); row=c.execute('SELECT likes FROM posts WHERE id=?',(pid,)).fetchone(); return jsonify(ok=True,liked=liked,likes=row['likes'] if row else 0)
@app.post('/api/posts/<int:pid>/comments')
@auth_required
def comment(pid):
    text=str(body().get('text','')).strip()
    if not text:return jsonify(ok=False,error='Comment is empty'),400
    c=db().execute('INSERT INTO comments(post_id,uid,text,created_at) VALUES(?,?,?,?)',(pid,uid(),text,timestamp())); db().commit(); return jsonify(ok=True,commentId=c.lastrowid),201
@app.get('/api/posts/<int:pid>/comments')
@auth_required
def comments(pid):
    rows=db().execute("SELECT c.*,COALESCE(NULLIF(u.nickname,''),u.name) AS name,u.profile_pic FROM comments c JOIN users u ON u.id=c.uid WHERE post_id=? ORDER BY c.id",(pid,)).fetchall(); return jsonify(ok=True,comments=[dict(x) for x in rows])
@app.post('/api/messages')
@auth_required
def message_send():
    d=body(); target=str(d.get('recipientUid','')).strip(); text=str(d.get('message','')).strip()
    if not target or not text:return jsonify(ok=False,error='Recipient and message required'),400
    cur=db().execute('INSERT INTO messages(sender,recipient,text,created_at) VALUES(?,?,?,?)',(uid(),target,text,timestamp())); db().commit(); return jsonify(ok=True,messageId=cur.lastrowid),201
@app.get('/api/messages/<target>')
@auth_required
def messages(target):
    rows=db().execute('SELECT * FROM messages WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?) ORDER BY id LIMIT 1000',(uid(),target,target,uid())).fetchall(); return jsonify(ok=True,messages=[dict(x) for x in rows])
@app.get('/api/notifications')
@auth_required
def notifications():
    rows=db().execute('SELECT * FROM notifications WHERE uid=? ORDER BY id DESC LIMIT 100',(uid(),)).fetchall(); return jsonify(ok=True,notifications=[dict(x) for x in rows])
@app.get('/api/wallet')
@auth_required
def wallet():
    rows=db().execute('SELECT txid,currency,created_at FROM tapjoy_rewards WHERE uid=? ORDER BY created_at DESC LIMIT 100',(uid(),)).fetchall(); return jsonify(ok=True,balance='0',providerCurrencyRecorded=sum(x['currency'] for x in rows),transactions=[dict(x) for x in rows],feePercent=10,network='ethereum-sepolia')
@app.get('/api/withdrawals')
@auth_required
def my_withdrawals():
    rows=db().execute('SELECT id,amount,fee,net_amount,currency,payout_method,status,admin_note,created_at,updated_at FROM withdrawal_requests WHERE uid=? ORDER BY id DESC LIMIT 100',(uid(),)).fetchall()
    return jsonify(ok=True,feePercent=WITHDRAWAL_FEE_PERCENT,requests=[dict(x) for x in rows])

@app.post('/api/withdrawals')
@auth_required
def create_withdrawal():
    d=body()
    try: amount=round(float(d.get('amount',0)),2)
    except (TypeError,ValueError): return jsonify(ok=False,error='Enter a valid withdrawal amount.'),400
    currency=str(d.get('currency','USD')).strip().upper()
    method=str(d.get('payoutMethod','')).strip()
    details=str(d.get('payoutDetails','')).strip()
    if amount<=0 or amount>1000000: return jsonify(ok=False,error='Amount must be greater than zero and within the allowed limit.'),400
    if currency not in ('USD','ZAR','SOV'): return jsonify(ok=False,error='Choose USD, ZAR, or SOV as the request currency.'),400
    if method not in ('Bank Transfer','PayPal','Crypto Wallet','QR Payment'): return jsonify(ok=False,error='Choose a supported payout method.'),400
    if len(details)<4 or len(details)>500: return jsonify(ok=False,error='Enter payout details (4–500 characters).'),400
    fee=round(amount*WITHDRAWAL_FEE_PERCENT/100,2); net=round(amount-fee,2); now=timestamp()
    cur=db().execute("INSERT INTO withdrawal_requests(uid,amount,fee,net_amount,currency,payout_method,payout_details,status,admin_note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'PENDING','',?,?)",(uid(),amount,fee,net,currency,method,details,now,now)); db().commit()
    request_id=cur.lastrowid
    email_lines=[
        'A new Scorpio Octavious Vibe withdrawal request needs admin review.', '',
        f'Request ID: {request_id}', f"User: {g.user['name']}", f"User email: {g.user['email']}",
        f'Amount requested: {amount:.2f} {currency}', f'10% fee: {fee:.2f} {currency}',
        f'Estimated payout after fee: {net:.2f} {currency}', f'Method: {method}',
        f'Payout details: {details}', 'Status: PENDING', '',
        'Review this request in the SOV admin dashboard. Do not mark it paid until the payout has actually been completed.'
    ]
    email_sent=send_withdrawal_email(f'SOV withdrawal request #{request_id}', '\n'.join(email_lines))
    return jsonify(ok=True,requestId=request_id,status='PENDING',amount=amount,fee=fee,netAmount=net,currency=currency,adminEmail=WITHDRAWAL_NOTIFY_EMAIL,emailSent=email_sent,message='Withdrawal request saved for admin review. Funds have not been paid and the available wallet balance is not verified by this request form.'),201

@app.get('/api/admin/withdrawals')
@auth_required
def admin_withdrawals():
    if not is_admin(): return jsonify(ok=False,error='Forbidden'),403
    rows=db().execute('SELECT w.*,u.name AS user_name,u.email AS user_email FROM withdrawal_requests w JOIN users u ON u.id=w.uid ORDER BY w.id DESC LIMIT 500').fetchall()
    return jsonify(ok=True,requests=[dict(x) for x in rows])

@app.post('/api/admin/withdrawals/<int:request_id>')
@auth_required
def admin_update_withdrawal(request_id):
    if not is_admin(): return jsonify(ok=False,error='Forbidden'),403
    d=body(); status=str(d.get('status','')).strip().upper(); note=str(d.get('adminNote','')).strip()[:1000]
    if status not in ('PENDING','APPROVED','REJECTED','PAID'): return jsonify(ok=False,error='Status must be PENDING, APPROVED, REJECTED, or PAID.'),400
    row=db().execute('SELECT * FROM withdrawal_requests WHERE id=?',(request_id,)).fetchone()
    if not row:return jsonify(ok=False,error='Withdrawal request not found.'),404
    db().execute('UPDATE withdrawal_requests SET status=?,admin_note=?,updated_at=? WHERE id=?',(status,note,timestamp(),request_id)); db().commit()
    return jsonify(ok=True,requestId=request_id,status=status,message='Status updated. This does not send money automatically.')

@app.post('/api/wallet/record-send')
@auth_required
def record_send():
    d=body(); fields=['sender','recipient','amount','fee','recipientTx','feeTx','network']
    if not all(str(d.get(x,'')).strip() for x in fields):return jsonify(ok=False,error='Incomplete wallet transfer'),400
    db().execute('INSERT INTO wallet_sends(uid,sender,recipient,amount,fee,recipient_tx,fee_tx,network,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(uid(),d['sender'],d['recipient'],str(d['amount']),str(d['fee']),d['recipientTx'],d['feeTx'],d['network'],timestamp())); db().commit(); return jsonify(ok=True,recorded=True)
@app.post('/api/boosts')
@auth_required
def boost():
    d=body(); packages={10000:10,30000:45,100000:100,300000:250}
    try: views=int(d.get('views',0)); amount=float(d.get('amount',0))
    except: return jsonify(ok=False,error='Invalid package'),400
    if views not in packages or round(amount,2)!=packages[views]:return jsonify(ok=False,error='Choose a valid boost package'),400
    cur=db().execute('INSERT INTO boosts(uid,views,amount,status,created_at) VALUES(?,?,?,?,?)',(uid(),views,amount,'PAYMENT_REQUIRED',timestamp())); db().commit(); return jsonify(ok=True,boostId=cur.lastrowid,status='PAYMENT_REQUIRED',message='Package recorded; this endpoint does not charge PayPal.'),201
@app.get('/api/boosts')
@auth_required
def my_boosts(): return jsonify(ok=True,campaigns=[dict(x) for x in db().execute('SELECT * FROM boosts WHERE uid=? ORDER BY id DESC',(uid(),)).fetchall()])
@app.get('/api/game-rewards')
@auth_required
def game_rewards():
    rows=db().execute('SELECT id,game_name,amount,currency,status,source_ref,created_at FROM game_rewards WHERE uid=? ORDER BY id DESC LIMIT 100',(uid(),)).fetchall()
    total=db().execute("SELECT COALESCE(SUM(amount),0) AS total FROM game_rewards WHERE uid=? AND status='VERIFIED'",(uid(),)).fetchone()['total']
    return jsonify(ok=True,rewards=[dict(r) for r in rows],verifiedTotal=total)

@app.post('/api/admin/game-rewards')
@auth_required
def admin_add_game_reward():
    if not is_admin(): return jsonify(ok=False,error='Forbidden'),403
    d=body(); target=str(d.get('userId','')).strip(); game=str(d.get('gameName','')).strip()[:100]; currency=str(d.get('currency','SOV')).strip().upper(); ref=str(d.get('sourceRef','')).strip()[:180]
    try: amount=round(float(d.get('amount',0)),6)
    except (TypeError,ValueError): return jsonify(ok=False,error='Enter a valid reward amount.'),400
    if not target or not game or not ref or amount<=0 or currency not in ('SOV','USD','ZAR','POINTS'): return jsonify(ok=False,error='User ID, game, positive amount, unique source reference, and supported currency are required.'),400
    if not db().execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(): return jsonify(ok=False,error='User not found.'),404
    try:
        db().execute("INSERT INTO game_rewards(uid,game_name,amount,currency,status,source_ref,created_at) VALUES(?,?,?,?,'VERIFIED',?,?)",(target,game,amount,currency,ref,timestamp())); db().commit()
    except sqlite3.IntegrityError: return jsonify(ok=False,error='That reward source reference was already recorded.'),409
    db().execute('INSERT INTO notifications(uid,text,created_at) VALUES(?,?,?)',(target,f'Game reward verified: {amount:g} {currency} from {game}.',timestamp())); db().commit()
    return jsonify(ok=True,status='VERIFIED',message='Reward recorded. This is a reward ledger entry, not an on-chain token transfer.'),201

@app.route('/api/tapjoy/callback',methods=['GET','POST'])
def tapjoy_callback():
    d=request.values; tx=str(d.get('id') or d.get('transaction_id') or '').strip(); user=str(d.get('snuid') or d.get('publisher_user_id') or '').strip(); currency=str(d.get('currency') or d.get('amount') or '0').strip(); verifier=str(d.get('verifier') or d.get('signature') or '').strip()
    if not all([tx,user,verifier,TAPJOY_SECRET]):return jsonify(ok=False,error='Missing callback fields or server secret'),400
    expected=hashlib.md5(f'{tx}:{user}:{currency}:{TAPJOY_SECRET}'.encode()).hexdigest()
    if not hmac.compare_digest(expected.lower(),verifier.lower()):return jsonify(ok=False,error='Invalid Tapjoy verifier'),403
    try: amount=float(currency)
    except:return jsonify(ok=False,error='Invalid currency'),400
    if amount<0:return jsonify(ok=False,error='Invalid currency'),400
    try: db().execute('INSERT INTO tapjoy_rewards VALUES(?,?,?,?,?)',(tx,user,amount,timestamp(),str(dict(d)))); db().commit()
    except sqlite3.IntegrityError:return jsonify(ok=True,success=True,duplicate=True)
    # Currency is recorded for reconciliation; this does not mint or transfer tokens.
    return jsonify(ok=True,success=True,duplicate=False,transactionId=tx,uid=user,currency=amount)
def is_admin(): return bool(ADMIN_EMAIL) and g.user.get('email','').lower()==ADMIN_EMAIL
@app.get('/api/admin/summary')
@auth_required
def admin_summary():
    if not is_admin():return jsonify(ok=False,error='Forbidden'),403
    c=db(); return jsonify(ok=True,users=c.execute('SELECT COUNT(*) n FROM users').fetchone()['n'],posts=c.execute('SELECT COUNT(*) n FROM posts').fetchone()['n'],tapjoyTransactions=c.execute('SELECT COUNT(*) n FROM tapjoy_rewards').fetchone()['n'],gameRewards=c.execute('SELECT COUNT(*) n FROM game_rewards').fetchone()['n'],tapjoyCurrencyRecorded=c.execute('SELECT COALESCE(SUM(currency),0) n FROM tapjoy_rewards').fetchone()['n'],boosts=c.execute('SELECT COUNT(*) n FROM boosts').fetchone()['n'],pendingWithdrawals=c.execute("SELECT COUNT(*) n FROM withdrawal_requests WHERE status='PENDING'").fetchone()['n'])
@app.get('/api/admin/users')
@auth_required
def admin_users():
    if not is_admin():return jsonify(ok=False,error='Forbidden'),403
    return jsonify(ok=True,users=[dict(x) for x in db().execute('SELECT id,email,name,nickname,profile_pic,created_at FROM users ORDER BY created_at DESC LIMIT 1000').fetchall()])
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
