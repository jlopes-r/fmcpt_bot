#!/usr/bin/env python3
"""
Instagram Cookie Renewal Script
Uses yt-dlp login to refresh sessionid cookies automatically.
Run via cron every 7 days to prevent expiration.
"""
import os
import sys
import re
import time
import json
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from datetime import datetime

# Paths
COOKIE_PATH = "/home/juanl/bot/data/instagram_cookies.txt"
SESSION_PATH = "/home/juanl/bot/data/ig_session.json"
LOG_FILE = "/home/juanl/bot/data/logs/cookie_renewal.log"

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def login_and_get_sessionid(username, password):
    """Login to Instagram using requests and extract sessionid."""
    import requests
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-IG-App-Locale": "en_US",
        "X-IG-Device-Locale": "en_US",
        "X-IG-Mapped-Locale": "en_US",
        "X-IG-App-ID": "936619743392459",
        "X-IG-Device-ID": "7179658273819460078",
        "X-IG-Android-ID": "android-7179658273819460078",
        "X-IG-Capabilities": "3brTvx8",
        "X-IG-Connection-Type": "WIFI",
        "X-Pigeon-Session-Id": "UFS-7179658273819460078-1",
        "X-Pigeon-Rawclienttime": str(time.time()),
        "X-IG-Timezone-Offset": "0",
        "X-IG-Connection-Speed": f"{random.randint(1000, 3700)}kbps",
        "Accept-Encoding": "gzip, deflate",
        "Host": "i.instagram.com",
        "Connection": "keep-alive",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })

    # Step 1: Get CSRF token and mid
    try:
        r = session.get("https://i.instagram.com/api/v1/si/fetch_headers/", params={
            "challenge_type": "signup",
            "guid": "7179658273819460078"
        })
        if r.status_code != 200:
            log(f"Failed to get headers: {r.status_code}")
            return None
    except Exception as e:
        log(f"Request error: {e}")
        return None

    # Step 2: Generate device info
    import hashlib
    import hmac
    import random
    
    generate_sessionid = str(random.randint(1000000000, 9999999999))
    
    # Step 3: Login
    data = {
        "phone_id": "7179658273819460078",
        "_csrftoken": session.cookies.get("csrftoken", ""),
        "username": username,
        "adid": "7179658273819460078",
        "guid": "7179658273819460078",
        "device_id": "android-7179658273819460078",
        "login_nonce": "",
        "password": password,
    }
    
    try:
        r = session.post("https://i.instagram.com/api/v1/accounts/login/", data=data)
        result = r.json()
        
        if result.get("status") != "ok":
            msg = result.get("message", "Unknown error")
            log(f"Login failed: {msg}")
            return None
        
        log("Login successful!")
        return session.cookies.get("sessionid")
        
    except Exception as e:
        log(f"Login request error: {e}")
        return None

def save_cookies_file(sessionid):
    """Save sessionid in Mozilla cookie format for yt-dlp."""
    cookie_data = (
        "# HTTP Cookie File\n"
        ".instagram.com\tTRUE\t/\tFALSE\t1893456000\tsessionid\t{sid}\n"
        ".instagram.com\tTRUE\t/\tFALSE\t0\tig_did\t{uuid}\n"
        ".instagram.com\tTRUE\t/\tFALSE\t0\tmid\t{mid}\n"
        ".instagram.com\tTRUE\t/\tFALSE\t0\tig_nrcb\t1\n"
    ).format(
        sid=sessionid,
        uuid="7179658273819460078",
        mid="Z6xQ2AALAAFHxQxQxQxQ"
    )
    
    try:
        with open(COOKIE_PATH, "w") as f:
            f.write(cookie_data)
        log(f"Cookies saved to {COOKIE_PATH}")
        return True
    except Exception as e:
        log(f"Failed to save cookies: {e}")
        return False

def test_cookies():
    """Test if current cookies work by trying to access a public post."""
    import requests
    if not os.path.exists(COOKIE_PATH):
        return False
    
    # Read sessionid
    with open(COOKIE_PATH) as f:
        content = f.read()
    
    match = re.search(r"sessionid\t([^\t\n]+)", content)
    if not match:
        return False
    
    sessionid = match.group(1)
    
    try:
        r = requests.get("https://www.instagram.com/p/CR1bQjXlqjG/", headers={
            "User-Agent": "Mozilla/5.0",
            "Cookie": f"sessionid={sessionid}"
        }, timeout=10)
        
        # If we get the post (not login redirect), cookies are valid
        return r.status_code == 200 and "instagram" in r.url.lower()
    except Exception:
        return False

def main():
    import random
    log("=" * 50)
    log("Starting Instagram cookie renewal")
    
    # Check if credentials are set
    username = os.getenv("IG_USERNAME")
    password = os.getenv("IG_PASSWORD")
    
    if not username or not password:
        log("IG_USERNAME and IG_PASSWORD not set in environment")
        log("Skipping auto-renewal. Please update cookies manually.")
        return
    
    # Test current cookies first
    if test_cookies():
        log("Current cookies are still valid. No renewal needed.")
        return
    
    log("Current cookies invalid. Attempting login...")
    
    # Login and get new sessionid
    sessionid = login_and_get_sessionid(username, password)
    if not sessionid:
        log("Failed to login. Manual cookie renewal required.")
        return
    
    # Save new cookies
    if save_cookies_file(sessionid):
        log("Cookie renewal completed successfully!")
        # Restart bot to use new cookies
        log("Restarting bot service...")
        os.system("sudo systemctl restart superbot.service")
    else:
        log("Failed to save cookies!")

if __name__ == "__main__":
    main()
