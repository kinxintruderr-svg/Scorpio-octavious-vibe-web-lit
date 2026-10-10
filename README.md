# Scorpio Octavious Vibe Lite (no Firebase)

Minimal SOV portal with a Render/Flask API, email/password accounts, Tapjoy server callback verification, MetaMask/Trust Wallet injected-provider connection, and PayPal LIVE boost checkout.

## Render deploy
- Create a new Render Web Service from this folder/repository.
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`
- Mount a persistent disk at `/var/data` (the included `render.yaml` does this).
- Set `APP_SECRET` to a long random private value; `TAPJOY_SECRET`; `TAPJOY_WEB_SDK_KEY`; `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` to **LIVE** credentials.
- Set `SOV_DB=/var/data/sov_lite.sqlite3`.
- Update `API_BASE` in `static/index.html` to the exact deployed Render service URL.
- Configure Tapjoy's server callback to `https://YOUR-RENDER-URL/api/tapjoy/callback` using Tapjoy's dashboard settings and verify its expected callback format before going live.

## Important limitations
- This removes Firebase entirely; accounts are managed by this API.
- The Tapjoy callback verifies and records provider currency with duplicate transaction protection. The button intentionally does not pretend a live offerwall is initialized: wire the official Tapjoy Web SDK and approved placement according to your Tapjoy account's current integration instructions before enabling the wall.
- PayPal checkout creates/captures LIVE orders for listed promotion packages. Paid orders are recorded; real ad-view delivery needs a separate, genuine delivery mechanism and is not simulated.
- MetaMask/Trust Wallet buttons connect injected EVM providers. They do not custody keys, send tokens, or claim to transfer SOV. Trust Wallet may require opening the site in its in-app browser.
- No token rewards are automatically minted/transferred from Tapjoy currency. Define an approved conversion/payout policy and secure treasury flow before promising users cash or tokens.
- SQLite is placed on a Render persistent disk. Back up the disk and use PostgreSQL if you need multi-instance scaling.
- Before public launch, add account recovery, rate limits, CSRF/origin protections where appropriate, provider webhooks/transaction reconciliation, and security review.
