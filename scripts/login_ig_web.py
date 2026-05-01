#!/usr/bin/env python3
"""Login to Instagram via web endpoint and generate full cookies for yt-dlp."""
import requests
import time
import json
import os

COOKIE_FILE = "/home/juanl/bot/data/instagram_cookies.txt"
USERNAME = os.getenv("IG_USERNAME", "")
PASSWORD = os.getenv("IG_PASSWORD", "")

def main():
    session = requests.Session()
    
    # Step 1: Get initial page to establish cookies
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    
    print("Getting initial cookies...")
    r = session.get("https://www.instagram.com/accounts/login/")
    if r.status_code != 200:
        print(f"Failed to load login page: {r.status_code}")
        return
    
    csrf = session.cookies.get("csrftoken")
    if not csrf:
        print("Failed to get CSRF token")
        return
    
    print(f"Got CSRF: {csrf[:20]}...")
    
    # Step 2: Login via web form
    login_url = "https://www.instagram.com/accounts/login/ajax/"
    data = {
        "username": USERNAME,
        "enc_password": f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{PASSWORD}",
        "queryParams": "{}",
        "optIntoOneTap": "false",
    }
    
    session.headers.update({
        "X-CSRFToken": csrf,
        "X-IG-App-ID": "936619743392459",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/accounts/login/",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    
    print("Logging in...")
    r = session.post(login_url, data=data)
    result = r.json()
    
    print(f"Login response: {json.dumps(result, indent=2)}")
    
    if not result.get("authenticated"):
        msg = result.get("message", "Unknown")
        print(f"Login failed: {msg}")
        
        # Check for challenge/challenge_required
        if result.get("challenge"):
            print("CHALLENGE REQUIRED - Instagram requires verification.")
            print("The easiest way is to export cookies from your browser:")
            print("1. Login to instagram.com in Chrome")
            print("2. Use extension 'Get cookies.txt LOCALLY'")
            print("3. Save as instagram_cookies.txt")
            print("4. Upload to the VM")
        return
    
    print("Login successful!")
    
    # Print all cookies
    print("\nCookies captured:")
    for cookie in session.cookies:
        print(f"  {cookie.name}: {cookie.value[:30]}...")
    
    # Generate cookies.txt
    expires = str(int(time.time()) + 90 * 24 * 3600)
    lines = ["# Netscape HTTP Cookie File"]
    
    for cookie in session.cookies:
        domain = f".{cookie.domain}" if not cookie.domain.startswith(".") else cookie.domain
        secure = "TRUE" if cookie.secure else "FALSE"
        path = cookie.path or "/"
        exp = str(int(cookie.expires)) if cookie.expires else "0"
        lines.append(f"{domain}\tTRUE\t{path}\t{secure}\t{exp}\t{cookie.name}\t{cookie.value}")
    
    with open(COOKIE_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
    
    print(f"\nSaved {len(lines)-1} cookies to {COOKIE_FILE}")
    
    # Verify the sessionid
    sid = session.cookies.get("sessionid")
    if sid:
        print(f"sessionid: {sid[:20]}... OK")
    else:
        print("WARNING: No sessionid found!")

if __name__ == "__main__":
    main()
