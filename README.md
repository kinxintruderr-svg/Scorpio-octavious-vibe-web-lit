# Scorpio Octavious Vibe Lite — frontend + backend foundation

This package combines the HTML pages with a Flask API backend. Fake local wallet opening balances and fake local earn points were removed: the wallet now reads the server ledger and the earn page reads verified server reward records. A new account starts with a zero ledger balance.

## Start backend locally
```bash
cd backend
python -m venv .venv
# activate the environment, then:
pip install -r requirements.txt
export JWT_SECRET="use-a-long-random-secret"
export GOOGLE_CLIENT_ID="your-web-client-id.apps.googleusercontent.com"
export ADMIN_EMAIL="your-admin-google-email@example.com"
python app.py
```

Set `config.js` `API_BASE_URL` to the backend origin. Configure the matching public Google Web Client ID in `login.html` and Google Cloud authorized origins. Serve the frontend over HTTPS (or localhost for local development); `file://` is not suitable for production auth.

## What is not live until configured
- Google Sign-In needs the correct OAuth client ID and authorized origins.
- Tapjoy reward callbacks are deliberately disabled until account-specific signature verification is added.
- Withdrawal requests are recorded for review; the backend does not send PayPal/crypto payments.
- SOV Crypto page options are UI only until wallet libraries, supported chain configuration, token contracts, and transaction verification are implemented.
- Boost Ads campaign payment and ad delivery require payment-provider verification and a real delivery system.
- Monthly email reporting needs a scheduler and configured email service.
- Most social page interactions remain front-end behavior and need additional API endpoints before shared multi-user functionality is real.

Never put secrets, seed phrases, private keys, or provider server keys in frontend HTML.
