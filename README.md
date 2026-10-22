# Scorpio Octavious Vibe Lite — Firebase-free starter

This starter replaces Firebase Auth/Firestore with a Flask API and SQLite account store. It includes navigable pages for Home/feed, Search, Reels, Friends/following, Messages, SOV Wallet/reward history, Tapjoy, Boost package requests, Profile, Notifications, Business placeholder, Settings and Admin.

## Frontend-to-API connection

The website frontend is hosted at `https://scorpio-octavious-vibe-web-lit.onrender.com`. Its browser requests are configured to use the separate backend API at `https://scorpio-octavious-vibe-api.onrender.com`. Do not use the website URL as the API base URL. The frontend HTML and `templates/index.html` use the same API endpoint. This ZIP change does not deploy to Render; redeploy the frontend service from this project for it to take effect online.

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

- Users can submit a withdrawal request with amount, currency, payout method, and payout details. The backend calculates a 50% fee and stores the request as `PENDING` for admin review.
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


## Google-only sign-in rebuild

This version removes email/password sign-up and login from the UI and disables those API endpoints. Users can browse public pages as guests; account-specific API routes still require a signed-in session. Google Sign-In uses Google Identity Services in the browser and verifies the ID token on the Flask backend.

### Deploy as a new Render service
1. Push this folder to a new GitHub repository, or upload its files to your existing SOV repository after backing up the current version.
2. In Render, create a new Web Service from the repository. `render.yaml` defines the service as `scorpio-octavious-vibe-lite-google`.
3. Set `GOOGLE_CLIENT_ID` to the Google **Web application OAuth client ID** used in `index.html`. Configure that OAuth client with the deployed website origin under Authorized JavaScript origins.
4. Set `JWT_SECRET` to a long random secret. Keep `SOV_DB=/var/data/sov.db` and attach the persistent disk defined in `render.yaml`.
5. Set optional provider secrets (`TAPJOY_SECRET`, `TAPJOY_WEB_SDK_KEY`, payment and SMTP variables) only when you have valid credentials.
6. Deploy and test `/api/health` and `/api/config`.

### Important limits
This ZIP is prepared source code, not a deployed service. Google Sign-In requires the OAuth client to authorize the live domain and `GOOGLE_CLIENT_ID` to match the client ID used by the frontend. Existing users from a previous database are preserved only if the new service uses that same persistent database; a separate Render service normally has its own disk. Tapjoy rewards, PayPal payments, wallet transfers, and boosts are not automatically live merely because their UI is present.


### Games & Rewards
The Games & Rewards page includes a Tapjoy offerwall launch button and verified reward history. Tapjoy requires a valid `TAPJOY_WEB_SDK_KEY`, an allowed/configured web domain and provider-side setup in Render/Tapjoy. Actual offers and rewards depend on provider availability; this UI does not fabricate games or earnings. Reward history is loaded from the existing `/api/game-rewards` route, and provider rewards must be verified before crediting.


## Boost Ads campaign tracking update

- The Boost Ads page supports the $10 USD / 10,000 target views package and other existing packages, optional campaign name and optional public HTTPS post link, and a per-user campaign history.
- New requests are stored as `PAYMENT_REQUIRED`. The API does not let the browser mark a campaign paid or active. Existing campaign rows/statuses are preserved with backward-compatible SQLite column migrations.
- **PayPal is not connected for checkout/capture by this change.** A configured client ID alone is not proof of a working live integration. Before activating campaigns, implement server-side PayPal order creation and capture, verify capture status with PayPal using server credentials, and only then change payment/campaign status. Do not launch ads based on a browser redirect or user-supplied status.
- The $10 package promises a target of 10,000 views, not guaranteed views. Campaign request creation does not charge the user or start delivery.


## Tapjoy Earn and withdrawal estimate fix

- The Tapjoy Earn page provides an offerwall launch action and refreshes the verified provider-currency records from the backend. Tapjoy must be configured with the matching web SDK key, app/placement, and allowed domain.
- Rewards are not promised on offerwall open; the backend callback must verify the provider signature/verifier and reject duplicate transaction IDs. Provider currency records do not by themselves represent on-chain SOV tokens or a cash payout.
- Fixed the withdrawal estimate display to calculate the configured 50% fee accurately (for example, $10 requested means $5 fee and $5 estimated payout). The backend fee configuration remains 50%.
- PayPal Boost Ads remains a request-tracking flow until server-side PayPal order creation and capture verification are configured and tested.
