"""Download the production database from https://diettracker.kndyman.com"""
import sys
import os
from pathlib import Path
from datetime import datetime
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

APP_PASSWORD = os.getenv("APP_PASSWORD")
PROD_URL = "https://diettracker.kndyman.com"

if not APP_PASSWORD:
    print("Error: APP_PASSWORD not found in .env file")
    sys.exit(1)

session = requests.Session()

# Login
print("Authenticating to production...")
try:
    login_resp = session.post(
        f"{PROD_URL}/api/auth/login",
        json={"password": APP_PASSWORD},
        verify=False,  # Ignore SSL warnings for self-signed certs
    )
    login_resp.raise_for_status()
    print("✓ Authentication successful")
except Exception as e:
    print(f"✗ Authentication failed: {e}")
    sys.exit(1)

# Download database
print("Downloading database...")
try:
    download_resp = session.get(
        f"{PROD_URL}/api/database/download",
        verify=False,
    )
    download_resp.raise_for_status()
    
    # Save to timestamped file
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    output_file = f"production_backup_{timestamp}.db"
    
    with open(output_file, "wb") as f:
        f.write(download_resp.content)
    
    file_size_mb = Path(output_file).stat().st_size / (1024 * 1024)
    print(f"✓ Database downloaded: {output_file} ({file_size_mb:.2f} MB)")
    
except Exception as e:
    print(f"✗ Download failed: {e}")
    sys.exit(1)
