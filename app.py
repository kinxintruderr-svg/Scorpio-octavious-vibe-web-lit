# Scorpio Octavious Vibe — FULL BACKEND
# app.py
#
# Required environment variables:
# SOV_AUTH_SECRET
# PAYPAL_CLIENT_ID
# PAYPAL_CLIENT_SECRET
# PAYPAL_RETURN_URL
# PAYPAL_CANCEL_URL
# TAPJOY_SECRET
# TAPJOY_WEB_SDK_KEY
# SOV_FEE_WALLET
# ADMIN_EMAIL
#
# Install:
# pip install flask flask-cors requests  gunicorn

import os
import json
import sqlite3
import hashlib
import hmac
import base64
import secrets
import time
import re
from datetime import datetime, timezone
from functools import wraps

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS



# ============================================================
# APP
# ============================================================

app = Flask(__name__)

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": "*",
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "allow_headers": ["Authorization", "Content-Type"],
        }
    },
)

PORT = int(os.getenv("PORT", "10000"))

DB_FILE = os.getenv("SOV_DB", "scorpio_sov.db")

ADMIN_EMAIL = os.getenv(
    "ADMIN_EMAIL",
    "kinxintruder@gmail.com"
)

SOV_FEE_WALLET = os.getenv(
    "SOV_FEE_WALLET",
    ""
)

TAPJOY_SECRET = os.getenv(
    "TAPJOY_SECRET",
    ""
)

TAPJOY_WEB_SDK_KEY = os.getenv(
    "TAPJOY_WEB_SDK_KEY",
    ""
)

PAYPAL_CLIENT_ID = os.getenv(
    "PAYPAL_CLIENT_ID",
    ""
)

PAYPAL_CLIENT_SECRET = os.getenv(
    "PAYPAL_CLIENT_SECRET",
    ""
)

PAYPAL_RETURN_URL = os.getenv(
    "PAYPAL_RETURN_URL",
    ""
)

PAYPAL_CANCEL_URL = os.getenv(
    "PAYPAL_CANCEL_URL",
    ""
)

PAYPAL_BASE = (
    "https://api-m.paypal.com"
)


# ============================================================
# DATABASE
# ============================================================

def database():

    connection = sqlite3.connect(
        DB_FILE
    )

    connection.row_factory = sqlite3.Row

    return connection


def now():

    return datetime.now(
        timezone.utc
    ).isoformat()


def initialize_database():

    connection = database()

    connection.executescript(
        """

        CREATE TABLE IF NOT EXISTS boost_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            paypal_order_id TEXT UNIQUE NOT NULL,

            uid TEXT NOT NULL,

            views INTEGER NOT NULL,

            amount_cents INTEGER NOT NULL,

            currency TEXT NOT NULL DEFAULT 'USD',

            status TEXT NOT NULL,

            created_at TEXT NOT NULL,

            updated_at TEXT NOT NULL,
            password_hash TEXT
        );


        CREATE TABLE IF NOT EXISTS tapjoy_rewards (
            transaction_id TEXT PRIMARY KEY,

            uid TEXT NOT NULL,

            currency REAL NOT NULL,

            status TEXT NOT NULL,

            raw_data TEXT,

            created_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS wallet_sends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            uid TEXT NOT NULL,

            sender TEXT NOT NULL,

            recipient TEXT NOT NULL,

            amount TEXT NOT NULL,

            fee TEXT NOT NULL,

            recipient_tx TEXT NOT NULL,

            fee_tx TEXT NOT NULL,

            network TEXT NOT NULL,

            created_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY,

            email TEXT,

            display_name TEXT,

            created_at TEXT NOT NULL,

            updated_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS boost_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            uid TEXT NOT NULL,

            paypal_order_id TEXT UNIQUE NOT NULL,

            views INTEGER NOT NULL,

            amount_cents INTEGER NOT NULL,

            delivered_views INTEGER NOT NULL DEFAULT 0,

            status TEXT NOT NULL,

            created_at TEXT NOT NULL,

            updated_at TEXT NOT NULL
        );

        """
    )

    connection.commit()

    connection.close()


initialize_database()

# Safe migration for existing Render SQLite databases.
with database() as _conn:
    _cols = {row[1] for row in _conn.execute("PRAGMA table_info(users)").fetchall()}
    if "password_hash" not in _cols:
        _conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")


# ============================================================
# AUTHENTICATION
# ============================================================

AUTH_SECRET = os.getenv("SOV_AUTH_SECRET", "")
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 14

def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _jwt_encode(payload):
    if not AUTH_SECRET:
        raise RuntimeError("SOV_AUTH_SECRET is not configured in Render")
    header = _b64url(json.dumps({"alg":"HS256","typ":"JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    message = f"{header}.{body}"
    signature = hmac.new(AUTH_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    return f"{message}.{_b64url(signature)}"

def _jwt_decode(token):
    if not AUTH_SECRET:
        raise ValueError("SOV_AUTH_SECRET is not configured")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed token")
    message = f"{parts[0]}.{parts[1]}"
    expected = _b64url(hmac.new(AUTH_SECRET.encode(), message.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, parts[2]):
        raise ValueError("Invalid token signature")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Token expired")
    return payload

def require_sov_user(function):
    # Authentication is handled by SOV-owned signed JWTs.
    @wraps(function)
    def wrapper(*args, **kwargs):
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return jsonify({"ok": False, "error": "Please log in first"}), 401
        try:
            decoded = _jwt_decode(authorization[7:].strip())
            request.sov_user = {"uid": decoded["sub"], "email": decoded.get("email", ""), "name": decoded.get("name", "")}
        except Exception:
            return jsonify({"ok": False, "error": "Invalid or expired SOV login token"}), 401
        return function(*args, **kwargs)
    return wrapper

def current_uid():
    return request.sov_user.get("uid", "")

@app.post("/api/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    display_name = str(data.get("displayName", data.get("display_name", ""))).strip()[:80]
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({"ok":False,"error":"Enter a valid email address"}), 400
    if len(password) < 8:
        return jsonify({"ok":False,"error":"Password must be at least 8 characters"}), 400
    if not display_name:
        display_name = email.split("@",1)[0][:80]
    conn = database()
    if conn.execute("SELECT 1 FROM users WHERE lower(email)=?", (email,)).fetchone():
        conn.close()
        return jsonify({"ok":False,"error":"An account with this email already exists"}), 409
    uid = secrets.token_urlsafe(18)
    pwd_hash = hashlib.scrypt(password.encode(), salt=(salt:=secrets.token_bytes(16)), n=16384, r=8, p=1).hex()
    stored_hash = salt.hex() + ":" + pwd_hash
    stamp = now()
    conn.execute("INSERT INTO users(uid,email,display_name,created_at,updated_at,password_hash) VALUES(?,?,?,?,?,?)", (uid,email,display_name,stamp,stamp,stored_hash))
    conn.commit(); conn.close()
    try:
        token = _jwt_encode({"sub":uid,"email":email,"name":display_name,"iat":int(time.time()),"exp":int(time.time())+TOKEN_TTL_SECONDS})
    except Exception as exc:
        return jsonify({"ok":False,"error":str(exc)}), 503
    return jsonify({"ok":True,"token":token,"user":{"uid":uid,"email":email,"displayName":display_name}}), 201

@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    conn = database()
    row = conn.execute("SELECT uid,email,display_name,password_hash FROM users WHERE lower(email)=?", (email,)).fetchone()
    conn.close()
    if not row or not row["password_hash"]:
        return jsonify({"ok":False,"error":"Email or password is incorrect"}), 401
    try:
        salt_hex, expected = row["password_hash"].split(":",1)
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1).hex()
        if not hmac.compare_digest(actual, expected):
            raise ValueError("bad password")
    except Exception:
        return jsonify({"ok":False,"error":"Email or password is incorrect"}), 401
    try:
        token = _jwt_encode({"sub":row["uid"],"email":row["email"],"name":row["display_name"] or "","iat":int(time.time()),"exp":int(time.time())+TOKEN_TTL_SECONDS})
    except Exception as exc:
        return jsonify({"ok":False,"error":str(exc)}), 503
    return jsonify({"ok":True,"token":token,"user":{"uid":row["uid"],"email":row["email"],"displayName":row["display_name"]}})

@app.get("/api/auth/me")
@require_sov_user
def auth_me():
    conn=database(); row=conn.execute("SELECT uid,email,display_name,created_at FROM users WHERE uid=?",(current_uid(),)).fetchone(); conn.close()
    if not row: return jsonify({"ok":False,"error":"Account not found"}),404
    return jsonify({"ok":True,"user":{"uid":row["uid"],"email":row["email"],"displayName":row["display_name"],"createdAt":row["created_at"]}})

# ============================================================
# HEALTH
# ============================================================

@app.get("/")
def home():

    return jsonify(
        {
            "ok": True,
            "name": "Scorpio Octavious Vibe WEB",
            "version": "5.0.0-no-firebase",
            "status": "online"
        }
    )


@app.get("/api/health")
def health():

    return jsonify(
        {
            "ok": True,
            "service": "Scorpio Octavious Vibe WEB",
            "status": "online",
            "time": now()
        }
    )


# ============================================================
# PUBLIC CONFIG
# ============================================================

@app.get("/api/config")
def public_config():

    return jsonify(
        {
            "ok": True,

            # Blockchain addresses are public information.
            # ADMIN EMAIL IS NEVER RETURNED HERE.
            "sovFeeWallet": SOV_FEE_WALLET,

            # This is intended to be a public Web SDK key.
            "tapjoyWebSdkKey": TAPJOY_WEB_SDK_KEY,

            "paypalLiveConfigured": bool(
                PAYPAL_CLIENT_ID
                and PAYPAL_CLIENT_SECRET
            ),

            "network": "ethereum-sepolia",

            "sovSendFeePercent": 50
        }
    )


# ============================================================
# USER REGISTRATION / PROFILE BACKEND
# ============================================================

@app.post("/api/user/sync")
@require_sov_user
def sync_user():

    uid = current_uid()

    email = (
        request.sov_user.get("email")
        or ""
    )

    display_name = (
        request.sov_user.get("name")
        or ""
    )

    connection = database()

    existing = connection.execute(
        """
        SELECT uid
        FROM users
        WHERE uid = ?
        """,
        (uid,)
    ).fetchone()

    if existing:

        connection.execute(
            """
            UPDATE users
            SET email = ?,
                display_name = ?,
                updated_at = ?
            WHERE uid = ?
            """,
            (
                email,
                display_name,
                now(),
                uid
            )
        )

    else:

        connection.execute(
            """
            INSERT INTO users
            (
                uid,
                email,
                display_name,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                uid,
                email,
                display_name,
                now(),
                now()
            )
        )

    connection.commit()

    connection.close()

    return jsonify(
        {
            "ok": True,
            "uid": uid
        }
    )


# ============================================================
# PAYPAL
# ============================================================

def paypal_access_token():

    if not PAYPAL_CLIENT_ID:

        raise RuntimeError(
            "PAYPAL_CLIENT_ID is missing"
        )

    if not PAYPAL_CLIENT_SECRET:

        raise RuntimeError(
            "PAYPAL_CLIENT_SECRET is missing"
        )

    response = requests.post(
        PAYPAL_BASE
        + "/v1/oauth2/token",

        auth=(
            PAYPAL_CLIENT_ID,
            PAYPAL_CLIENT_SECRET
        ),

        data={
            "grant_type":
                "client_credentials"
        },

        headers={
            "Accept":
                "application/json",

            "Accept-Language":
                "en_US"
        },

        timeout=30
    )

    response.raise_for_status()

    return response.json()["access_token"]


# ============================================================
# CREATE BOOST PAYPAL ORDER
# ============================================================

@app.post("/api/paypal/create-order")
@require_sov_user
def create_paypal_order():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:

        views = int(
            data.get(
                "views",
                0
            )
        )

        amount = float(
            data.get(
                "amount",
                0
            )
        )

    except Exception:

        return jsonify(
            {
                "ok": False,
                "error": "Invalid package"
            }
        ), 400

    if views <= 0:

        return jsonify(
            {
                "ok": False,
                "error": "Views must be greater than zero"
            }
        ), 400

    if amount <= 0:

        return jsonify(
            {
                "ok": False,
                "error": "Amount must be greater than zero"
            }
        ), 400

    # Keep prices controlled by backend.
    # Users cannot simply send an arbitrary cheap amount.

    allowed_packages = {
        10000: 10.00,
        30000: 45.00,
        100000: 100.00,
        300000: 250.00
    }

    expected_price = allowed_packages.get(
        views
    )

    if expected_price is None:

        return jsonify(
            {
                "ok": False,
                "error": "Boost package is not available"
            }
        ), 400

    if round(amount, 2) != round(
        expected_price,
        2
    ):

        return jsonify(
            {
                "ok": False,
                "error": "Incorrect boost price"
            }
        ), 400

    try:

        token = paypal_access_token()

    except Exception as exc:

        return jsonify(
            {
                "ok": False,
                "error": "PayPal configuration error",
                "details": str(exc)
            }
        ), 503

    order_body = {

        "intent": "CAPTURE",

        "purchase_units": [

            {
                "reference_id":
                    "SOV-BOOST",

                "description":
                    (
                        "Scorpio Octavious Vibe "
                        f"Boost - {views:,} views"
                    ),

                "amount": {

                    "currency_code":
                        "USD",

                    "value":
                        f"{amount:.2f}"
                }
            }

        ],

        "application_context": {

            "brand_name":
                "Scorpio Octavious Vibe",

            "user_action":
                "PAY_NOW",

            "return_url":
                PAYPAL_RETURN_URL,

            "cancel_url":
                PAYPAL_CANCEL_URL
        }
    }

    try:

        response = requests.post(

            PAYPAL_BASE
            + "/v2/checkout/orders",

            json=order_body,

            headers={

                "Authorization":
                    f"Bearer {token}",

                "Content-Type":
                    "application/json",

                "Accept":
                    "application/json"
            },

            timeout=30
        )

    except Exception as exc:

        return jsonify(
            {
                "ok": False,
                "error": "Could not reach PayPal",
                "details": str(exc)
            }
        ), 502

    if not response.ok:

        return jsonify(
            {
                "ok": False,
                "error":
                    "PayPal order creation failed",

                "paypal":
                    response.text[:1000]
            }
        ), 502

    paypal_data = response.json()

    order_id = paypal_data.get(
        "id"
    )

    if not order_id:

        return jsonify(
            {
                "ok": False,
                "error":
                    "PayPal returned no order ID"
            }
        ), 502

    approval_url = ""

    for link in paypal_data.get(
        "links",
        []
    ):

        if link.get("rel") == "approve":

            approval_url = link.get(
                "href",
                ""
            )

            break

    connection = database()

    connection.execute(

        """
        INSERT INTO boost_orders
        (
            paypal_order_id,
            uid,
            views,
            amount_cents,
            currency,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,

        (
            order_id,
            current_uid(),
            views,
            round(amount * 100),
            "USD",
            "CREATED",
            now(),
            now()
        )
    )

    connection.commit()

    connection.close()

    return jsonify(
        {
            "ok": True,
            "orderId": order_id,
            "approvalUrl": approval_url
        }
    )


# ============================================================
# CAPTURE PAYPAL ORDER
# ============================================================

@app.post(
    "/api/paypal/capture-order/<order_id>"
)
@require_sov_user
def capture_paypal_order(
    order_id
):

    connection = database()

    order = connection.execute(

        """
        SELECT *
        FROM boost_orders
        WHERE paypal_order_id = ?
          AND uid = ?
        """,

        (
            order_id,
            current_uid()
        )

    ).fetchone()

    if not order:

        connection.close()

        return jsonify(
            {
                "ok": False,
                "error": "Order not found"
            }
        ), 404

    if order["status"] == "COMPLETED":

        connection.close()

        return jsonify(
            {
                "ok": True,
                "status": "COMPLETED",
                "alreadyCaptured": True
            }
        )

    try:

        token = paypal_access_token()

    except Exception as exc:

        connection.close()

        return jsonify(
            {
                "ok": False,
                "error":
                    "PayPal configuration error",
                "details": str(exc)
            }
        ), 503

    try:

        response = requests.post(

            PAYPAL_BASE
            + f"/v2/checkout/orders/"
            + order_id
            + "/capture",

            headers={

                "Authorization":
                    f"Bearer {token}",

                "Content-Type":
                    "application/json",

                "Accept":
                    "application/json"
            },

            timeout=30
        )

    except Exception as exc:

        connection.close()

        return jsonify(
            {
                "ok": False,
                "error":
                    "Could not reach PayPal",
                "details": str(exc)
            }
        ), 502

    if not response.ok:

        connection.close()

        return jsonify(
            {
                "ok": False,
                "error":
                    "PayPal capture failed",
                "paypal":
                    response.text[:1000]
            }
        ), 502

    result = response.json()

    status = result.get(
        "status",
        "UNKNOWN"
    )

    connection.execute(

        """
        UPDATE boost_orders
        SET status = ?,
            updated_at = ?
        WHERE paypal_order_id = ?
        """,

        (
            status,
            now(),
            order_id
        )
    )

    if status == "COMPLETED":

        connection.execute(

            """
            INSERT OR REPLACE INTO boost_campaigns
            (
                uid,
                paypal_order_id,
                views,
                amount_cents,
                delivered_views,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,

            (
                current_uid(),
                order_id,
                order["views"],
                order["amount_cents"],
                0,
                "PAID",
                now(),
                now()
            )
        )

    connection.commit()

    connection.close()

    return jsonify(
        {
            "ok":
                status == "COMPLETED",

            "status":
                status,

            "orderId":
                order_id
        }
    )


# ============================================================
# TAPJOY CALLBACK
# ============================================================

def verify_tapjoy_callback(
    transaction_id,
    uid,
    currency,
    verifier
):

    if not TAPJOY_SECRET:

        return False

    message = (
        f"{transaction_id}:"
        f"{uid}:"
        f"{currency}:"
        f"{TAPJOY_SECRET}"
    )

    expected = hashlib.md5(
        message.encode("utf-8")
    ).hexdigest()

    return hmac.compare_digest(
        expected.lower(),
        str(verifier).lower()
    )


@app.get("/api/tapjoy/rewards")
@require_sov_user
def tapjoy_user_rewards():
    """Return only the signed-in user's verified Tapjoy postback records."""
    conn = database()
    rows = conn.execute(
        "SELECT transaction_id, currency, status, created_at "
        "FROM tapjoy_rewards WHERE uid = ? ORDER BY created_at DESC LIMIT 200",
        (current_uid(),)
    ).fetchall()
    total = conn.execute(
        "SELECT COALESCE(SUM(currency), 0) AS total, COUNT(*) AS count "
        "FROM tapjoy_rewards WHERE uid = ? AND status = 'VERIFIED'",
        (current_uid(),)
    ).fetchone()
    conn.close()
    return jsonify({
        "ok": True,
        "rewards": [dict(row) for row in rows],
        "totalCurrency": float(total["total"] or 0),
        "count": int(total["count"] or 0),
        "unit": "Tapjoy provider currency units"
    })


@app.route(
    "/api/tapjoy/callback",
    methods=["GET", "POST"]
)
def tapjoy_callback():

    values = request.values

    transaction_id = (
        values.get("id")
        or values.get("transaction_id")
                            or ""
    ).strip()

    uid = (
        values.get("snuid")
        or values.get("publisher_user_id")
        or ""
    ).strip()

    currency = (
        values.get("currency")
        or values.get("amount")
        or "0"
    ).strip()

    verifier = (
        values.get("verifier")
        or values.get("signature")
        or ""
    ).strip()

    if not transaction_id:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Missing transaction ID"
            }
        ), 400

    if not uid:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Missing publisher user ID"
            }
        ), 400

    if not verifier:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Missing verifier"
            }
        ), 400

    try:

        currency_number = float(
            currency
        )

    except Exception:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Invalid currency"
            }
        ), 400

    if currency_number < 0:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Invalid reward amount"
            }
        ), 400

    valid = verify_tapjoy_callback(
        transaction_id,
        uid,
        currency,
        verifier
    )

    if not valid:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Invalid Tapjoy verifier"
            }
        ), 403

    connection = database()

    existing = connection.execute(

        """
        SELECT transaction_id
        FROM tapjoy_rewards
        WHERE transaction_id = ?
        """,

        (
            transaction_id,
        )

    ).fetchone()

    if existing:

        connection.close()

        return jsonify(
            {
                "ok": True,
                "success": True,
                "duplicate": True
            }
        )

    raw_data = json.dumps(
        {
            key: values.get(key)
            for key in values.keys()
        }
    )

    connection.execute(

        """
        INSERT INTO tapjoy_rewards
        (
            transaction_id,
            uid,
            currency,
            status,
            raw_data,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,

        (
            transaction_id,
            uid,
            currency_number,
            "VERIFIED",
            raw_data,
            now()
        )
    )

    connection.commit()

    connection.close()

    # IMPORTANT:
    # This verifies and records the Tapjoy reward.
    # It does NOT falsely claim an on-chain SOV transfer.
    #
    # A separate treasury service must mint/send actual SOV.

    return jsonify(
        {
            "ok": True,
            "success": True,
            "duplicate": False,
            "transactionId":
                transaction_id,
            "uid":
                uid,
            "currency":
                currency_number
        }
    )

# ============================================================
# REAL BITCOIN MAINNET
# ============================================================

BITCOIN_API_URL = os.getenv(
    "BITCOIN_API_URL",
    "https://blockstream.info/api"
).rstrip("/")


def bitcoin_get(path):
    response = requests.get(
        BITCOIN_API_URL + path,
        timeout=20,
        headers={
            "Accept": "application/json"
        }
    )

    response.raise_for_status()

    return response.json()


# ------------------------------------------------------------
# BITCOIN NETWORK STATUS
# ------------------------------------------------------------

@app.get("/api/btc/network")
def btc_network():

    try:

        height = bitcoin_get(
            "/blocks/tip/height"
        )

        block_hash = bitcoin_get(
            "/blocks/tip/hash"
        )

        fees = bitcoin_get(
            "/fee-estimates"
        )

        return jsonify({
            "ok": True,
            "network": "bitcoin-mainnet",
            "chain": "main",
            "blockHeight": height,
            "latestBlockHash": block_hash,
            "feeEstimates": fees
        })

    except Exception as exc:

        return jsonify({
            "ok": False,
            "network": "bitcoin-mainnet",
            "error": "Bitcoin mainnet unavailable",
            "details": str(exc)
        }), 503


# ------------------------------------------------------------
# BITCOIN ADDRESS
# ------------------------------------------------------------

@app.get("/api/btc/address/<address>")
def btc_address(address):

    try:

        info = bitcoin_get(
            f"/address/{address}"
        )

        transactions = bitcoin_get(
            f"/address/{address}/txs"
        )

        utxos = bitcoin_get(
            f"/address/{address}/utxo"
        )

        chain = info.get(
            "chain_stats",
            {}
        )

        mempool = info.get(
            "mempool_stats",
            {}
        )

        received = int(
            chain.get(
                "funded_txo_sum",
                0
            )
        )

        spent = int(
            chain.get(
                "spent_txo_sum",
                0
            )
        )

        balance_sats = (
            received - spent
        )

        return jsonify({

            "ok": True,

            "network":
                "bitcoin-mainnet",

            "address":
                address,

            "balanceSatoshis":
                balance_sats,

            "balanceBTC":
                balance_sats / 100000000,

            "mempoolReceivedSatoshis":
                int(
                    mempool.get(
                        "funded_txo_sum",
                        0
                    )
                ),

            "mempoolSpentSatoshis":
                int(
                    mempool.get(
                        "spent_txo_sum",
                        0
                    )
                ),

            "transactions":
                transactions[:25],

            "utxos":
                utxos
        })

    except requests.HTTPError:

        return jsonify({
            "ok": False,
            "error":
                "Invalid or unknown Bitcoin address"
        }), 400

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error":
                "Bitcoin address lookup failed",
            "details":
                str(exc)
        }), 503


# ------------------------------------------------------------
# BITCOIN TRANSACTION
# ------------------------------------------------------------

@app.get("/api/btc/tx/<txid>")
def btc_transaction(txid):

    try:

        transaction = bitcoin_get(
            f"/tx/{txid}"
        )

        status = bitcoin_get(
            f"/tx/{txid}/status"
        )

        return jsonify({

            "ok": True,

            "network":
                "bitcoin-mainnet",

            "transaction":
                transaction,

            "status":
                status
        })

    except requests.HTTPError:

        return jsonify({
            "ok": False,
            "error":
                "Bitcoin transaction not found"
        }), 404

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error":
                "Bitcoin transaction lookup failed",
            "details":
                str(exc)
        }), 503

# ============================================================
# WALLET SEND RECORD
# ============================================================

@app.post(
    "/api/wallet/record-send"
)
@require_sov_user
def record_wallet_send():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    sender = str(
        data.get("sender", "")
    ).strip()

    recipient = str(
        data.get("recipient", "")
    ).strip()

    amount = str(
        data.get("amount", "")
    ).strip()

    fee = str(
        data.get("fee", "")
    ).strip()

    recipient_tx = str(
        data.get("recipientTx", "")
    ).strip()

    fee_tx = str(
        data.get("feeTx", "")
    ).strip()

    network = str(
        data.get("network", "")
    ).strip()

    if not all(
        [
            sender,
            recipient,
            amount,
            fee,
            recipient_tx,
            fee_tx,
            network
        ]
    ):

        return jsonify(
            {
                "ok": False,
                "error":
                    "Incomplete wallet transfer"
            }
        ), 400

    connection = database()

    connection.execute(

        """
        INSERT INTO wallet_sends
        (
            uid,
            sender,
            recipient,
            amount,
            fee,
            recipient_tx,
            fee_tx,
            network,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,

        (
            current_uid(),
            sender,
            recipient,
            amount,
            fee,
            recipient_tx,
            fee_tx,
            network,
            now()
        )
    )

    connection.commit()

    connection.close()

    return jsonify(
        {
            "ok": True,
            "recorded": True
        }
    )

# ============================================================
# ADMIN
# ============================================================

def is_admin():

    email = (
        request.sov_user.get(
            "email"
        )
        or ""
    ).lower()

    return hmac.compare_digest(
        email,
        ADMIN_EMAIL.lower()
    )


@app.get(
    "/api/admin/summary"
)
@require_sov_user
def admin_summary():

    if not is_admin():

        return jsonify(
            {
                "ok": False,
                "error": "Forbidden"
            }
        ), 403

    connection = database()

    rewards = connection.execute(

        """
        SELECT
            COUNT(*) AS transactions,
            COALESCE(
                SUM(currency),
                0
            ) AS total
        FROM tapjoy_rewards
        """
    ).fetchone()

    orders = connection.execute(

        """
        SELECT COUNT(*) AS total
        FROM boost_orders
        """
    ).fetchone()

    campaigns = connection.execute(

        """
        SELECT COUNT(*) AS total
        FROM boost_campaigns
        """
    ).fetchone()

    sends = connection.execute(

        """
        SELECT COUNT(*) AS total
        FROM wallet_sends
        """
    ).fetchone()

    connection.close()

    return jsonify(
        {
            "ok": True,

            "tapjoy": {
                "transactions":
                    rewards["transactions"],

                "currencyRecorded":
                    rewards["total"]
            },

            "paypal": {
                "orders":
                    orders["total"]
            },

            "boost": {
                "campaigns":
                    campaigns["total"]
            },

            "wallet": {
                "sovTransfers":
                    sends["total"]
            }
        }
    )


# ============================================================
# ADMIN BOOST CAMPAIGNS
# ============================================================

@app.get(
    "/api/admin/boosts"
)
@require_sov_user
def admin_boosts():

    if not is_admin():

        return jsonify(
            {
                "ok": False,
                "error": "Forbidden"
            }
        ), 403

    connection = database()

    rows = connection.execute(

        """
        SELECT *
        FROM boost_campaigns
        ORDER BY id DESC
        LIMIT 500
        """
    ).fetchall()

    connection.close()

    return jsonify(
        {
            "ok": True,
            "campaigns":
                [dict(row) for row in rows]
        }
    )


# ============================================================
# ADMIN TAPJOY REWARDS
# ============================================================

@app.get(
    "/api/admin/tapjoy"
)
@require_sov_user
def admin_tapjoy():

    if not is_admin():

        return jsonify(
            {
                "ok": False,
                "error": "Forbidden"
            }
        ), 403

    connection = database()

    rows = connection.execute(

        """
        SELECT *
        FROM tapjoy_rewards
        ORDER BY created_at DESC
        LIMIT 500
        """
    ).fetchall()

    connection.close()

    return jsonify(
        {
            "ok": True,
            "rewards":
                [dict(row) for row in rows]
        }
    )


# ============================================================
# ADMIN WALLET SENDS
# ============================================================

@app.get(
    "/api/admin/wallet-sends"
)
@require_sov_user
def admin_wallet_sends():

    if not is_admin():

        return jsonify(
            {
                "ok": False,
                "error": "Forbidden"
            }
        ), 403

    connection = database()

    rows = connection.execute(

        """
        SELECT *
        FROM wallet_sends
        ORDER BY created_at DESC
        LIMIT 500
        """
    ).fetchall()

    connection.close()

    return jsonify(
        {
            "ok": True,
            "transfers":
                [dict(row) for row in rows]
        }
    )


# ============================================================
# RUN
# ============================================================

# ============================================================
# EXTRA SOV SOCIAL API ROUTES
# ============================================================

def json_body():
    return request.get_json(silent=True) or {}


# ============================================================
# PROFILE
# ============================================================

@app.get("/api/profile/<uid>")
@require_sov_user
def get_profile(uid):

    connection = database()

    row = connection.execute(
        """
        SELECT uid, email, display_name,
               created_at, updated_at
        FROM users
        WHERE uid = ?
        """,
        (uid,)
    ).fetchone()

    connection.close()

    if not row:
        return jsonify({
            "ok": False,
            "error": "Profile not found"
        }), 404

    return jsonify({
        "ok": True,
        "profile": dict(row)
    })


@app.put("/api/profile")
@require_sov_user
def update_profile():

    data = json_body()

    display_name = str(
        data.get("displayName", "")
    ).strip()

    connection = database()

    connection.execute(
        """
        UPDATE users
        SET display_name = ?,
            updated_at = ?
        WHERE uid = ?
        """,
        (
            display_name,
            now(),
            current_uid()
        )
    )

    connection.commit()
    connection.close()

    return jsonify({
        "ok": True,
        "updated": True
    })


# ============================================================
# USERS / SEARCH
# ============================================================

@app.get("/api/users/search")
@require_sov_user
def search_users():

    query_text = str(
        request.args.get("q", "")
    ).strip()

    if len(query_text) < 2:
        return jsonify({
            "ok": True,
            "users": []
        })

    connection = database()

    rows = connection.execute(
        """
        SELECT uid, email, display_name,
               created_at
        FROM users
        WHERE lower(display_name)
              LIKE lower(?)
           OR lower(email)
              LIKE lower(?)
        ORDER BY display_name ASC
        LIMIT 50
        """,
        (
            "%" + query_text + "%",
            "%" + query_text + "%"
        )
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "users": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# FOLLOWING
# ============================================================

@app.post("/api/follow/<uid>")
@require_sov_user
def follow_user(uid):

    if uid == current_uid():
        return jsonify({
            "ok": False,
            "error": "Cannot follow yourself"
        }), 400

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS follows (
            follower_uid TEXT NOT NULL,
            following_uid TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (
                follower_uid,
                following_uid
            )
        )
        """
    )

    connection.execute(
        """
        INSERT OR IGNORE INTO follows
        (
            follower_uid,
            following_uid,
            created_at
        )
        VALUES (?, ?, ?)
        """,
        (
            current_uid(),
            uid,
            now()
        )
    )

    connection.commit()
    connection.close()

    return jsonify({
        "ok": True,
        "following": True
    })


@app.delete("/api/follow/<uid>")
@require_sov_user
def unfollow_user(uid):

    connection = database()

    connection.execute(
        """
        DELETE FROM follows
        WHERE follower_uid = ?
          AND following_uid = ?
        """,
        (
            current_uid(),
            uid
        )
    )

    connection.commit()
    connection.close()

    return jsonify({
        "ok": True,
        "following": False
    })


@app.get("/api/following")
@require_sov_user
def following():

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS follows (
            follower_uid TEXT NOT NULL,
            following_uid TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (
                follower_uid,
                following_uid
            )
        )
        """
    )

    rows = connection.execute(
        """
        SELECT u.uid,
               u.display_name,
               u.email,
               f.created_at
        FROM follows f
        JOIN users u
          ON u.uid = f.following_uid
        WHERE f.follower_uid = ?
        ORDER BY f.created_at DESC
        LIMIT 500
        """,
        (current_uid(),)
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "users": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# POSTS / FEED
# ============================================================

@app.post("/api/posts")
@require_sov_user
def create_post():

    data = json_body()

    text_content = str(
        data.get("text", "")
    ).strip()

    media_url = str(
        data.get("mediaUrl", "")
    ).strip()

    media_type = str(
        data.get("mediaType", "")
    ).strip()

    if not text_content and not media_url:
        return jsonify({
            "ok": False,
            "error": "Post cannot be empty"
        }), 400

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            text TEXT,
            media_url TEXT,
            media_type TEXT,
            likes INTEGER NOT NULL DEFAULT 0,
            views INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor = connection.execute(
        """
        INSERT INTO posts
        (
            uid,
            text,
            media_url,
            media_type,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            current_uid(),
            text_content,
            media_url,
            media_type,
            now()
        )
    )

    post_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return jsonify({
        "ok": True,
        "postId": post_id
    }), 201


@app.get("/api/posts")
@require_sov_user
def get_posts():

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            text TEXT,
            media_url TEXT,
            media_type TEXT,
            likes INTEGER NOT NULL DEFAULT 0,
            views INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    rows = connection.execute(
        """
        SELECT *
        FROM posts
        ORDER BY id DESC
        LIMIT 100
        """
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "posts": [
            dict(row)
            for row in rows
        ]
    })


@app.post("/api/posts/<int:post_id>/like")
@require_sov_user
def like_post(post_id):

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS post_likes (
            post_id INTEGER NOT NULL,
            uid TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(post_id, uid)
        )
        """
    )

    existing = connection.execute(
        """
        SELECT 1
        FROM post_likes
        WHERE post_id = ?
          AND uid = ?
        """,
        (
            post_id,
            current_uid()
        )
    ).fetchone()

    if existing:

        connection.execute(
            """
            DELETE FROM post_likes
            WHERE post_id = ?
              AND uid = ?
            """,
            (
                post_id,
                current_uid()
            )
        )

        connection.execute(
            """
            UPDATE posts
            SET likes =
                CASE
                    WHEN likes > 0
                    THEN likes - 1
                    ELSE 0
                END
            WHERE id = ?
            """,
            (post_id,)
        )

        liked = False

    else:

        connection.execute(
            """
            INSERT INTO post_likes
            VALUES (?, ?, ?)
            """,
            (
                post_id,
                current_uid(),
                now()
            )
        )

        connection.execute(
            """
            UPDATE posts
            SET likes = likes + 1
            WHERE id = ?
            """,
            (post_id,)
        )

        liked = True

    connection.commit()

    row = connection.execute(
        """
        SELECT likes
        FROM posts
        WHERE id = ?
        """,
        (post_id,)
    ).fetchone()

    connection.close()

    return jsonify({
        "ok": True,
        "liked": liked,
        "likes": row["likes"] if row else 0
    })


# ============================================================
# COMMENTS
# ============================================================

@app.post("/api/posts/<int:post_id>/comments")
@require_sov_user
def add_comment(post_id):

    data = json_body()

    text_content = str(
        data.get("text", "")
    ).strip()

    if not text_content:
        return jsonify({
            "ok": False,
            "error": "Comment is empty"
        }), 400

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            uid TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor = connection.execute(
        """
        INSERT INTO comments
        (
            post_id,
            uid,
            text,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            post_id,
            current_uid(),
            text_content,
            now()
        )
    )

    connection.commit()

    comment_id = cursor.lastrowid

    connection.close()

    return jsonify({
        "ok": True,
        "commentId": comment_id
    }), 201


@app.get("/api/posts/<int:post_id>/comments")
@require_sov_user
def get_comments(post_id):

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            uid TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    rows = connection.execute(
        """
        SELECT *
        FROM comments
        WHERE post_id = ?
        ORDER BY id ASC
        LIMIT 500
        """,
        (post_id,)
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "comments": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# MESSAGES
# ============================================================

@app.post("/api/messages")
@require_sov_user
def send_message():

    data = json_body()

    recipient = str(
        data.get("recipientUid", "")
    ).strip()

    message = str(
        data.get("message", "")
    ).strip()

    if not recipient or not message:
        return jsonify({
            "ok": False,
            "error": "Recipient and message required"
        }), 400

    if recipient == current_uid():
        return jsonify({
            "ok": False,
            "error": "Cannot message yourself"
        }), 400

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_uid TEXT NOT NULL,
            recipient_uid TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor = connection.execute(
        """
        INSERT INTO messages
        (
            sender_uid,
            recipient_uid,
            message,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            current_uid(),
            recipient,
            message,
            now()
        )
    )

    connection.commit()

    message_id = cursor.lastrowid

    connection.close()

    return jsonify({
        "ok": True,
        "messageId": message_id
    }), 201


@app.get("/api/messages/<uid>")
@require_sov_user
def get_messages(uid):

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_uid TEXT NOT NULL,
            recipient_uid TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    rows = connection.execute(
        """
        SELECT *
        FROM messages
        WHERE
        (
            sender_uid = ?
            AND recipient_uid = ?
        )
        OR
        (
            sender_uid = ?
            AND recipient_uid = ?
        )
        ORDER BY id ASC
        LIMIT 1000
        """,
        (
            current_uid(),
            uid,
            uid,
            current_uid()
        )
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "messages": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# NOTIFICATIONS
# ============================================================

@app.get("/api/notifications")
@require_sov_user
def notifications():

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            type TEXT NOT NULL,
            message TEXT NOT NULL,
            read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    rows = connection.execute(
        """
        SELECT *
        FROM notifications
        WHERE uid = ?
        ORDER BY id DESC
        LIMIT 100
        """,
        (current_uid(),)
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "notifications": [
            dict(row)
            for row in rows
        ]
    })


@app.post("/api/notifications/read")
@require_sov_user
def mark_notifications_read():

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            type TEXT NOT NULL,
            message TEXT NOT NULL,
            read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        UPDATE notifications
        SET read = 1
        WHERE uid = ?
        """,
        (current_uid(),)
    )

    connection.commit()
    connection.close()

    return jsonify({
        "ok": True
    })


# ============================================================
# WALLET INFORMATION
# ============================================================

@app.get("/api/wallet")
@require_sov_user
def wallet():

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_balances (
            uid TEXT PRIMARY KEY,
            balance TEXT NOT NULL DEFAULT '0',
            updated_at TEXT NOT NULL
        )
        """
    )

    row = connection.execute(
        """
        SELECT *
        FROM wallet_balances
        WHERE uid = ?
        """,
        (current_uid(),)
    ).fetchone()

    if not row:

        connection.execute(
            """
            INSERT INTO wallet_balances
            (
                uid,
                balance,
                updated_at
            )
            VALUES (?, ?, ?)
            """,
            (
                current_uid(),
                "0",
                now()
            )
        )

        connection.commit()

        balance = "0"

    else:
        balance = row["balance"]

    connection.close()

    return jsonify({
        "ok": True,
        "uid": current_uid(),
        "balance": balance,
        "feePercent": 50,
        "network": "ethereum-sepolia",
        "feeWallet": SOV_FEE_WALLET
    })


# ============================================================
# BOOST CAMPAIGNS
# ============================================================

@app.get("/api/boosts")
@require_sov_user
def user_boosts():

    connection = database()

    rows = connection.execute(
        """
        SELECT *
        FROM boost_campaigns
        WHERE uid = ?
        ORDER BY id DESC
        LIMIT 100
        """,
        (current_uid(),)
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "campaigns": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# ADMIN USERS
# ============================================================

@app.get("/api/admin/users")
@require_sov_user
def admin_users():

    if not is_admin():
        return jsonify({
            "ok": False,
            "error": "Forbidden"
        }), 403

    connection = database()

    rows = connection.execute(
        """
        SELECT
            uid,
            email,
            display_name,
            created_at,
            updated_at
        FROM users
        ORDER BY created_at DESC
        LIMIT 5000
        """
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "users": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# ADMIN PAYPAL / BOOST ORDERS
# ============================================================

@app.get("/api/admin/orders")
@require_sov_user
def admin_orders():

    if not is_admin():
        return jsonify({
            "ok": False,
            "error": "Forbidden"
        }), 403

    connection = database()

    rows = connection.execute(
        """
        SELECT *
        FROM boost_orders
        ORDER BY created_at DESC
        LIMIT 5000
        """
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "orders": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# ADMIN WITHDRAWALS
# ============================================================

@app.get("/api/admin/withdrawals")
@require_sov_user
def admin_withdrawals():

    if not is_admin():
        return jsonify({
            "ok": False,
            "error": "Forbidden"
        }), 403

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            amount TEXT NOT NULL,
            network TEXT NOT NULL,
            destination TEXT NOT NULL,
            fee TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    rows = connection.execute(
        """
        SELECT *
        FROM withdrawals
        ORDER BY id DESC
         LIMIT 5000
        """
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "orders": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# ADMIN WITHDRAWALS
# ============================================================

@app.get("/api/admin/withdrawals")
@require_sov_user
def admin_withdrawals():

    if not is_admin():
        return jsonify({
            "ok": False,
            "error": "Forbidden"
        }), 403

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            amount TEXT NOT NULL,
            network TEXT NOT NULL,
            destination TEXT NOT NULL,
            fee TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    rows = connection.execute(
        """
        SELECT *
        FROM withdrawals
        ORDER BY id DESC
        LIMIT 5000
        """
    ).fetchall()

    connection.close()

    return jsonify({
        "ok": True,
        "withdrawals": [
            dict(row)
            for row in rows
        ]
    })


# ============================================================
# WITHDRAWAL REQUEST
# ============================================================

@app.post("/api/withdraw")
@require_sov_user
def request_withdrawal():

    data = json_body()

    amount = str(
        data.get("amount", "")
    ).strip()

    network = str(
        data.get("network", "")
    ).strip()

    destination = str(
        data.get("destination", "")
    ).strip()

    if not amount or not network or not destination:
        return jsonify({
            "ok": False,
            "error": "Amount, network and destination are required"
        }), 400

    try:
        amount_number = float(amount)
    except Exception:
        return jsonify({
            "ok": False,
            "error": "Invalid amount"
        }), 400

    if amount_number <= 0:
        return jsonify({
            "ok": False,
            "error": "Amount must be greater than zero"
        }), 400

    fee = amount_number * 0.10
    net_amount = amount_number - fee

    connection = database()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            amount TEXT NOT NULL,
            network TEXT NOT NULL,
            destination TEXT NOT NULL,
            fee TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor = connection.execute(
        """
        INSERT INTO withdrawals
        (
            uid,
            amount,
            network,
            destination,
            fee,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            current_uid(),
            str(amount_number),
            network,
            destination,
            str(fee),
            "PENDING",
            now()
        )
    )

    connection.commit()

    withdrawal_id = cursor.lastrowid

    connection.close()

    return jsonify({
        "ok": True,
        "withdrawalId": withdrawal_id,
        "amount": amount_number,
        "fee": fee,
        "netAmount": net_amount,
        "status": "PENDING"
    }), 201


if __name__ == "__main__":

    print(
        "Scorpio Octavious Vibe web starting..."
    )

    print(
        "Port:",
        PORT
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False
    )

