# Scorpio Octavious Vibe Lite — Firebase-free starter

This starter replaces Firebase Auth/Firestore with a Flask API and SQLite account store. It includes navigable pages for Home/feed, Search, Reels, Friends/following, Messages, SOV Wallet/reward history, Tapjoy, Boost package requests, Profile, Notifications, Business placeholder, Settings and Admin.

## Run locally

```bash
pip install -r requirements.txt
export JWT_SECRET='use-a-long-random-secret'
export ADMIN_EMAIL='kinxintruder@gmail.com'
export WITHDRAWAL_NOTIFY_EMAIL='kinxintruder@gmail.com'
# Configure SMTP to email the admin when a withdrawal request is submitted
export SMTP_HOST='smtp.gmail.com'
export SMTP_PORT='587'
export SMTP_USERNAME='your-sending-gmail-address'
export SMTP_PASSWORD='your-Google-app-password'
export SMTP_FROM='your-sending-gmail-address'
export TAPJOY_SECRET='your-Tapjoy-postback-secret'
export TAPJOY_WEB_SDK_KEY='your-public-web-sdk-key'
python app.py
```

Open http://localhost:10000.

## Render deployment

Deploy this folder as a Python web service using `pip install -r requirements.txt` and `gunicorn app:app`. Set the environment variables above in Render. The Render blueprint declares a 1 GB persistent disk mounted at `/var/data` for SQLite. If you deploy without that disk, SQLite data may be lost on redeploy/restart; for higher traffic, migrate to Render Postgres. Render's default filesystem is ephemeral; a SQLite database stored there can be lost after restarts/redeploys unless a persistent disk is attached. See https://render.com/docs/disks and https://render.com/docs/deploys.

## Withdrawal requests

- Users can submit a withdrawal request with amount, currency, payout method, and payout details. The backend calculates a 10% fee and stores the request as `PENDING` for admin review.
- Admin dashboard access is restricted to the account whose email matches `ADMIN_EMAIL` (default: `kinxintruder@gmail.com`). The admin can set requests to `APPROVED`, `REJECTED`, or `PAID`; status changes do not transfer money automatically.
- Email alerts go to `WITHDRAWAL_NOTIFY_EMAIL` (default: `kinxintruder@gmail.com`) only when `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM` are configured. For Gmail, use a Google App Password, never your normal account password. The app reports whether the email was sent.
- Important: this starter does not have a reconciled user wallet ledger or automated balance reservation, so an admin must verify the user's real available balance and payout details before approving/paying. Do not treat a request as proof of earnings or funds.

## Important limits (not production-complete)

- Firebase is not used by this project.
- Tapjoy callback verifies the configured MD5 verifier and records currency once by transaction ID. It does **not** mint or transfer SOV tokens. Verify callback format with Tapjoy before production.
- The page now loads the official Unity/Tapjoy Web Offerwall SDK (`https://rewards.unity.com/owp/web/sdk/latest`) and initializes it with the configured public SDK key, the logged-in user's unique backend ID, and placement name `SOV offerwall`. The key, app/placement settings, and allowed domain must match your Tapjoy dashboard; test a real eligible offer before launch.
- Boost package requests are recorded as `PAYMENT_REQUIRED`; PayPal charging/capture is not connected in this starter.
- SOV wallet display is a reward-log summary, not an on-chain token balance. Wallet connection only connects the browser wallet; token transfers require a separately reviewed contract flow.
- Media upload storage, business listings, real-time chat, block/unblock, and notification generation need additional backend/storage work.
- Use HTTPS in production, a strong secret, rate limiting, verified email/password reset, and a persistent database before inviting users.


## Nickname, profile photos, and game rewards update

- Users can save a 1–40 character nickname; it is shown in profile, feed, search, comments, and following lists.
- Profile photos accept JPG, PNG, WEBP, and GIF up to 5 MB. Files are stored under `SOV_UPLOAD_DIR`; on Render this defaults to `/var/data/uploads` so the attached persistent disk can keep them between deploys.
- The new **Games & Rewards** page lists verified reward ledger entries for the signed-in user. This starter does not fabricate game payouts. Rewards must be recorded after a provider verifies them, or by an authenticated admin through the Admin page using a unique source reference. These ledger entries are not blockchain transfers.
- Navigation includes Games & Rewards and continues to share the same backend/session.

### Deploy notes

Keep the Render persistent disk mounted at `/var/data`. The `ADMIN_EMAIL` environment variable is `kinxintruder@gmail.com`; the admin must log in with that account. Configure SMTP environment variables if withdrawal emails should be delivered. Install dependencies with `pip install -r requirements.txt`.

The backend could not be executed in the editing environment because Flask is not installed there. Python syntax and inline JavaScript syntax checks passed; deploy and test against a staging database before relying on real rewards or payouts.
