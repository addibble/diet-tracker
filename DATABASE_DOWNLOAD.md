# Production Database Download

The diet tracker now includes a secure API endpoint to download the production SQLite database for local development and backup purposes.

## Overview

- **Endpoint:** `GET /api/database/download`
- **Authentication:** Required (uses session cookie)
- **Response:** SQLite database file (binary)
- **URL:** `https://diettracker.kndyman.com/api/database/download`

## How to Download

### Option 1: Using Python Script (Recommended)

```bash
python download_production_db.py
```

This script:
1. Reads `APP_PASSWORD` from your `.env` file
2. Authenticates to the production server
3. Downloads the database
4. Saves it with a timestamped filename: `production_backup_YYYY-MM-DD_HHMMSS.db`

**Requirements:**
- `.env` file must exist in the repository root with `APP_PASSWORD`
- Python 3.8+
- `requests` and `python-dotenv` packages

### Option 2: Using PowerShell

```powershell
.\download_production_db.ps1
```

This script:
1. Reads `APP_PASSWORD` from `.env`
2. Logs in and captures the session cookie
3. Downloads the database
4. Saves with a timestamped filename

### Option 3: Manual CURL

```bash
# 1. Login to get a session cookie
curl -X POST https://diettracker.kndyman.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"password": "YOUR_PASSWORD"}' \
  -c cookies.txt \
  -k  # Ignore SSL warnings

# 2. Download the database
curl -X GET https://diettracker.kndyman.com/api/database/download \
  -b cookies.txt \
  -o production_backup.db \
  -k
```

## Security

- The endpoint requires **authentication** using the `APP_PASSWORD` from `.env`
- A valid session cookie (set via `/api/auth/login`) is required
- The database file is downloaded over HTTPS
- Consider deleting downloaded backup files when no longer needed

## Usage Examples

### Sync Local Development Database

```bash
# Download production database
python download_production_db.py

# Rename to expected location
mv production_backup_*.db data/diet_tracker.db

# Restart local development server
python -m uvicorn app.main:app --reload
```

### Backup Before Major Changes

```bash
# Download as backup before migration or feature development
python download_production_db.py

# Keep the timestamped file for reference
```

### Database Size and Performance

The database file size depends on:
- Number of meals and food logs
- Workout history
- User preferences and settings
- Historical weight logs

Typical download time:
- < 1 MB: < 1 second
- 1-10 MB: 1-5 seconds
- 10+ MB: 5-30 seconds

## Troubleshooting

### Authentication Failed

```
Error: Authentication failed: HTTP Error 401
```

**Solutions:**
1. Verify `APP_PASSWORD` in `.env` file is correct
2. Verify `.env` file exists in repository root
3. Check production server is accessible

### Database Not Found (404)

```
Error: Download failed: HTTP Error 404
```

**Solutions:**
1. Verify production server is running
2. Check the endpoint URL is correct
3. Confirm you're authenticated (check session cookie)

### SSL Certificate Error

If you get SSL verification errors:

**Python:**
```python
# Edit download_production_db.py to add:
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
```

**PowerShell:**
```powershell
# Already handled with -SkipCertificateCheck flag
```

**CURL:**
```bash
# Use the -k flag (already shown above)
```

## File Management

Downloaded backup files are timestamped to prevent accidental overwrites:

```
production_backup_2024-01-15_143022.db
production_backup_2024-01-16_091545.db
```

To list all backups:

```bash
ls -la production_backup_*.db
```

To delete old backups:

```bash
# Keep only the 3 most recent
ls -t production_backup_*.db | tail -n +4 | xargs rm
```

## Integration with Git

The `.gitignore` file should exclude downloaded database backups:

```gitignore
production_backup_*.db
```

Never commit production databases to the repository.

## Related

- **Backend Endpoint:** `backend/app/routers/database.py`
- **Authentication:** `backend/app/auth.py`
- **Database Configuration:** `backend/app/database.py`
