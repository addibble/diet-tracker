# Production Database Download

The app now runs in multi-tenant mode: each user has their own SQLite database
under `data/users/<user_id>/diet_tracker.db`. To grab a snapshot of every
user's DB at once, use the admin endpoint below.

## Endpoint

- **URL:** `GET /api/admin/users/download-all`
- **Auth:** `require_admin_or_basic` — either an admin passkey-session cookie,
  or HTTP Basic auth using `LOGS_USER` / `LOGS_PASSWORD` from `.env`.
- **Response:** a zip with one snapshot per user plus a `manifest.json`.

Per-user snapshot: `GET /api/admin/users/<user_id>/download-db` (same auth).

## How to download

```bash
python download_production_db.py
```

This reads `APP_URL`, `LOGS_USER`, `LOGS_PASSWORD` from `.env`, downloads the
zip, and extracts it next to itself as `production_backup_<timestamp>/`.

### Manual curl

```bash
source .env
curl -u "$LOGS_USER:$LOGS_PASSWORD" \
  "$APP_URL/api/admin/users/download-all" \
  -o production_backup.zip
unzip production_backup.zip -d production_backup/
```

## Note on the old password-auth endpoint

The legacy `/api/database/download` + `/api/auth/login` flow no longer exists.
The app's interactive auth is passkey-based; the admin download endpoint is
guarded by HTTP Basic so scripts and CI can still pull snapshots without a
WebAuthn ceremony.
