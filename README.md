# Scorpio Octavious Vibe Lite — No Firebase

## What's in this project
- `index.html`: black/blue responsive SOV Lite frontend.
- `app.py`: Flask API with SQLite storage and SOV-owned email/password auth (scrypt password hashes + signed JWTs). Firebase SDK/admin authentication dependencies removed.
- Existing Tapjoy callback path remains `/api/tapjoy/callback`.
- Existing posts, likes/comments, messaging, profile, wallet, boosts, PayPal and admin routes retained where present in the supplied backend.

## Deploy on Render
1. Put this folder in a GitHub repository.
2. Create a Render Web Service from that repository. Render detects `render.yaml`.
3. Add `SOV_AUTH_SECRET` as a long random secret in Render Environment. Do not expose it in HTML.
4. Configure `TAPJOY_SECRET` and `TAPJOY_WEB_SDK_KEY` in Render. Keep the Tapjoy secret private.
5. Set the Tapjoy callback URL to `https://YOUR-RENDER-HOST/api/tapjoy/callback`.
6. Configure PayPal live credentials only when ready to accept live payments.

## Important deployment notes
- SQLite on a standard Render web service may not persist across redeploys/restarts unless a persistent disk is mounted. For a production social app, configure a persistent disk or migrate to a managed PostgreSQL database before relying on user data.
- Existing accounts created only in Firebase do not automatically migrate. Users need to register again unless you implement a secure account migration.
- The Tapjoy callback in the supplied backend verifies and records rewards; it does not mint/send SOV on-chain. The UI does not pretend otherwise. The actual approved Tapjoy Web SDK launch setup must be confirmed/configured in your Tapjoy publisher dashboard.
- The existing wallet/send fee logic and Sepolia network labels are preserved from the supplied backend; do not treat them as production-mainnet token transfer enforcement.
- This is a fresh Firebase-free starting project, not a claim that every provider feature is already live.
