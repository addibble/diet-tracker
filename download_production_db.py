"""Download per-user production databases from the diet-tracker app.

Uses the admin zip endpoint at ``/api/admin/users/download-all`` which is
guarded by ``require_admin_or_basic`` and accepts HTTP Basic auth using the
``LOGS_USER``/``LOGS_PASSWORD`` env vars. Saves a timestamped .zip and (if
``unzip`` is available) extracts it next to the archive.
"""
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

APP_URL = os.getenv("APP_URL", "https://diettracker.kndyman.com").rstrip("/")
LOGS_USER = os.getenv("LOGS_USER")
LOGS_PASSWORD = os.getenv("LOGS_PASSWORD")

if not LOGS_USER or not LOGS_PASSWORD:
    print("Error: LOGS_USER and LOGS_PASSWORD must be set in .env")
    sys.exit(1)

ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
out_zip = Path(f"production_backup_{ts}.zip")

print(f"Downloading {APP_URL}/api/admin/users/download-all ...")
try:
    resp = requests.get(
        f"{APP_URL}/api/admin/users/download-all",
        auth=(LOGS_USER, LOGS_PASSWORD),
        timeout=300,
        verify=False,
    )
    resp.raise_for_status()
except Exception as e:
    print(f"✗ Download failed: {e}")
    sys.exit(1)

out_zip.write_bytes(resp.content)
size_mb = out_zip.stat().st_size / (1024 * 1024)
print(f"✓ Saved {out_zip} ({size_mb:.2f} MB)")

extract_dir = out_zip.with_suffix("")
extract_dir.mkdir(exist_ok=True)
with zipfile.ZipFile(out_zip) as z:
    z.extractall(extract_dir)
print(f"✓ Extracted to {extract_dir}/")
