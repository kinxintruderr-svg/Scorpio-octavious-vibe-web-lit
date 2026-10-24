# SOV Lite Boost Ads payment backend update

This update preserves the existing Google-auth, wallet ledger, withdrawal policy (50% fee / US$90 minimum), earn summary, and admin endpoints, and adds server-side PayPal Orders API routes for Boost Ads.

## Important limits
- This is backend code; it does not automatically update `boost-ads.html` to call these routes.
- PayPal cannot be live until you set your own PayPal REST app Live Client ID and Secret in Render and switch `PAYPAL_MODE=live`. Never paste secrets into HTML or chat.
- Start with `PAYPAL_MODE=sandbox` and test a complete sandbox payment first.
- The capture route verifies PayPal's server response, status, currency, amount, and campaign ownership before marking payment paid.
- `delivery_status=queued` means payment verified and awaiting an actual campaign-delivery system. It does not mean views have started or are guaranteed.
- Crypto endpoint intentionally fails closed. Do not accept crypto funds until a receiving address, supported chain/token contracts, RPC/indexer, confirmation policy, and server-side transaction verifier are implemented.
- Use persistent storage on Render or migrate SQLite to managed PostgreSQL; an ephemeral disk may lose records on redeploy.
- Existing `render.yaml` root directory was `backend`; this standalone backend folder uses `rootDir: .`. If copying into an existing repository's `backend/` folder, retain that repository's appropriate root directory.

## Routes added
- `GET /api/boost/packages`
- `POST /api/boost/paypal/create-order` (requires `Authorization: Bearer <JWT>`, body `{ "package_code": "boost_10" }`)
- `POST /api/boost/paypal/capture` (requires JWT, body `{ "campaign_id": "...", "order_id": "..." }`)
- `GET /api/boost/campaigns` (requires JWT)
- `POST /api/boost/crypto/create-intent` (returns 503 until verified crypto payment integration is configured)

Package prices are server-controlled:
- `boost_10`: $10 / 10,000 target views
- `boost_20`: $20 / 25,000 target views
- `boost_35`: $35 / 50,000 target views
- `boost_60`: $60 / 100,000 target views
- `boost_250`: $250 / 500,000 target views

## Render environment variables
Keep the existing variables: `JWT_SECRET`, `GOOGLE_CLIENT_ID`, `ADMIN_EMAIL`, `CORS_ORIGINS`, `DATABASE_PATH` (if used).
Add:
- `PAYPAL_CLIENT_ID`: PayPal REST app client ID for the selected mode
- `PAYPAL_CLIENT_SECRET`: PayPal REST app secret for the selected mode
- `PAYPAL_MODE`: `sandbox` during testing; `live` only with Live credentials and after tests
- `PAYPAL_CURRENCY`: `USD` by default
- `FRONTEND_ORIGIN`: `https://scorpio-octavious-vibe-web-lit.onrender.com`

Do not set `PAYPAL_MODE=live` while using sandbox credentials. Do not store secrets in frontend code.

## Required remaining work
1. Configure and test PayPal sandbox orders/captures.
2. Connect `boost-ads.html` to create-order, redirect the user to `approve_url`, then call capture with `campaign_id` and `order_id` after the return flow. Do not trust a query-string success flag as payment proof.
3. Add verified webhooks/reconciliation for production robustness before launch.
4. Implement actual ad-delivery and view measurement before representing campaigns as delivering views.
5. Implement blockchain-specific crypto verification before accepting crypto payments.
