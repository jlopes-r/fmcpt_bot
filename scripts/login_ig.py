#!/usr/bin/env python3
"""Login to Instagram using instaloader and generate cookies.txt for yt-dlp."""
import instaloader
import os
import uuid

SESSION_FILE = "/home/juanl/bot/data/instaloader_session"
COOKIE_FILE = "/home/juanl/bot/data/instagram_cookies.txt"

USERNAME = os.getenv("IG_USERNAME", "")
PASSWORD = os.getenv("IG_PASSWORD", "")

L = instaloader.Instaloader()

try:
    # Try loading existing session first
    if os.path.exists(SESSION_FILE):
        try:
            L.load_session_from_file(SESSION_FILE)
            # Test if session is still valid
            L.check_profile_id("instagram")
            print("Existing session is still valid!")
        except Exception:
            print("Existing session expired. Logging in...")
            L.login(USERNAME, PASSWORD)
            L.save_session_to_file(SESSION_FILE)
    else:
        print("Logging in to Instagram...")
        L.login(USERNAME, PASSWORD)
        L.save_session_to_file(SESSION_FILE)

    # Get cookies from the session
    cookies = L.context._session.cookies
    
    sessionid = cookies.get("sessionid")
    csrftoken = cookies.get("csrftoken", "")
    mid = cookies.get("mid", "")
    ig_did = cookies.get("ig_did", "")
    rur = cookies.get("rur", "")

    print(f"sessionid: {sessionid[:20]}...")
    print(f"csrftoken: {csrftoken[:20]}...")
    print(f"mid: {mid}")
    print(f"ig_did: {ig_did}")
    print(f"rur: {rur}")

    # Generate cookies.txt in Mozilla/Netscape format for yt-dlp
    # Cookies expire in ~90 days
    import time
    expires = str(int(time.time()) + 90 * 24 * 3600)

    cookie_lines = ["# Netscape HTTP Cookie File"]
    
    # Required cookies for Instagram
    if csrftoken:
        cookie_lines.append(f".instagram.com\tTRUE\t/\tFALSE\t{expires}\tcsrftoken\t{csrftoken}")
    if mid:
        cookie_lines.append(f".instagram.com\tTRUE\t/\tFALSE\t0\tmid\t{mid}")
    if ig_did:
        cookie_lines.append(f".instagram.com\tTRUE\t/\tFALSE\t0\tig_did\t{ig_did}")
    if sessionid:
        cookie_lines.append(f".instagram.com\tTRUE\t/\tFALSE\t{expires}\tsessionid\t{sessionid}")
    if rur:
        cookie_lines.append(f".instagram.com\tTRUE\t/\tFALSE\t{expires}\trur\t{rur}")

    # Write cookies file
    with open(COOKIE_FILE, "w") as f:
        f.write("\n".join(cookie_lines) + "\n")
    
    print(f"\nCookies saved to {COOKIE_FILE}")
    print(f"Total cookies: {len(cookie_lines) - 1}")

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
