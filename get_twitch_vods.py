import requests
import time
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv

# ================= CONFIG =================

load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
SCOPES = "user:read:follows"

TOKEN_FILE = Path("twitch_token.json")
CACHE_FILE = Path("vod_cache.json")

HELIX = "https://api.twitch.tv/helix"
DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# ================= UTIL =================

def utcnow():
    return datetime.now(timezone.utc)

def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


# ================= TOKEN HANDLING =================

def save_token(token):
    token["obtained_at"] = int(time.time())
    TOKEN_FILE.write_text(json.dumps(token, indent=2))

def load_token():
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None

def token_valid(token):
    expires_in = token.get("expires_in", 0)
    obtained_at = token.get("obtained_at", 0)
    return time.time() < obtained_at + expires_in - 60

def device_auth():
    r = requests.post(
        DEVICE_URL,
        data={"client_id": CLIENT_ID, "scopes": SCOPES},
    ).json()

    print("\nAuthorize this app:")
    print(f"  Visit: {r['verification_uri']}")
    print(f"  Enter code: {r['user_code']}\n")

    while True:
        time.sleep(r["interval"])
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "device_code": r["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        if resp.status_code == 200:
            token = resp.json()
            save_token(token)
            return token


def refresh_token(token):
    if "refresh_token" not in token:
        return None

    r = requests.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        },
    )
    if r.status_code == 200:
        token = r.json()
        save_token(token)
        return token
    return None


def get_token():
    token = load_token()
    if token and token_valid(token):
        return token
    if token:
        refreshed = refresh_token(token)
        if refreshed:
            return refreshed
    return device_auth()


# ================= CACHE HANDLING =================


def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {"requests": []}


def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def print_cache_summary(cache):
    if not cache["requests"]:
        print("No saved requests.\n")
        return

    print("\nSaved requests:")
    for r in cache["requests"]:
        print(
            f"  [{r['id']}] requested_at={r['requested_at']} "
            f"delta={r['timedelta_days']}d vods={len(r['vods'])}"
        )
    print()


def show_request(cache, req_id):
    for r in cache["requests"]:
        if r["id"] == req_id:
            for v in r["vods"]:
                print(f"{v['channel']}: {v['title']}")
                print(f"  {v['url']}\n")
            return
    print("Request not found.\n")


# ================= TWITCH API =================


def helix_headers(token):
    return {
        "Authorization": f"Bearer {token['access_token']}",
        "Client-Id": CLIENT_ID,
    }


def get_user_id(headers):
    r = requests.get(f"{HELIX}/users", headers=headers)
    return r.json()["data"][0]["id"]


def get_followed(headers, user_id):
    r = requests.get(
        f"{HELIX}/channels/followed",
        headers=headers,
        params={"user_id": user_id, "first": 100},
    )
    return r.json()["data"]


def get_recent_vods(headers, broadcaster_id, since):
    r = requests.get(
        f"{HELIX}/videos",
        headers=headers,
        params={"user_id": broadcaster_id, "type": "archive", "first": 5},
    )
    vods = []
    for v in r.json()["data"]:
        created = datetime.fromisoformat(v["created_at"].replace("Z", "+00:00"))
        if created >= since:
            vods.append(
                {
                    "title": v["title"],
                    "url": v["url"],
                    "created_at": v["created_at"],
                }
            )
    return vods


# ================= MAIN =================


def main():
    cache = load_cache()

    if cache["requests"]:
        last = cache["requests"][-1]
        print(
            f"\nLast request was at {last['requested_at']} "
            f"(delta {last['timedelta_days']} days)"
        )
        print("Options:")
        print("  [1] Load last saved request (no API)")
        print("  [2] Make a new request")
        print("  [3] List all saved requests")
        print("  [4] View a specific request")
        choice = input("Choose: ").strip()

        if choice == "1":
            show_request(cache, last["id"])
            return
        if choice == "3":
            print_cache_summary(cache)
            return
        if choice == "4":
            req_id = int(input("Request ID: "))
            show_request(cache, req_id)
            return

    # New request
    days = input("How many days back? ")
    
    
    try:
        if int(days) > 0 or int(days) < 8:
            days = int(days)
        else:
            days = 3
    except:
        days = 3
    
    since = utcnow() - timedelta(days=days)

    token = get_token()
    headers = helix_headers(token)

    user_id = get_user_id(headers)
    followed = get_followed(headers, user_id)

    vods = []

    for ch in followed:
        recent = get_recent_vods(headers, ch["broadcaster_id"], since)
        for v in recent:
            vods.append(
                {
                    "channel": ch["broadcaster_name"],
                    **v,
                }
            )

    req = {
        "id": len(cache["requests"]) + 1,
        "requested_at": iso(utcnow()),
        "since": iso(since),
        "timedelta_days": days,
        "vods": vods,
    }

    cache["requests"].append(req)
    save_cache(cache)

    print(f"\nSaved request {req['id']} ({len(vods)} VODs)\n")
    show_request(cache, req["id"])


if __name__ == "__main__":
    main()