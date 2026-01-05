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

def pause():
    input("\nPress Enter to continue...")


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
            vods = r["vods"]

            include = choose_channels(vods)
            if include:
                vods = filter_vods(vods, include=include)

            print(f"\nShowing {len(vods)} VODs\n")
            for v in vods:
                print(f"{v['channel']}: {v['title']}")
                print(f"  {v['url']}\n")
            return

    print("Request not found.\n")



def list_channels(vods):
    return sorted({v["channel"] for v in vods})


def parse_selection(selection, max_index):
    chosen = set()

    for part in selection.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            start, end = part.split("-", 1)
            chosen.update(range(int(start), int(end) + 1))
        else:
            chosen.add(int(part))

    return [i for i in chosen if 1 <= i <= max_index]

def choose_channels(vods):
    channels = list_channels(vods)

    if not channels:
        print("No channels found.")
        return None  # no filter

    print("\nAvailable channels:")
    for i, ch in enumerate(channels, start=1):
        print(f"  [{i}] {ch}")

    selection = input(
        "\nSelect channels by number "
        "(e.g. 1,3-5 or Enter for all): "
    ).strip()

    if not selection:
        return None  # no filter

    indexes = parse_selection(selection, len(channels))
    return [channels[i - 1] for i in indexes]


def filter_vods(vods, include=None):
    """
    include: list of channel names (case-insensitive)
    """
    if not include:
        return vods

    include = {c.lower() for c in include}

    return [
        v for v in vods
        if v["channel"].lower() in include
    ]


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

    while True:
        print("\n===== Twitch VOD Tool =====")

        if cache["requests"]:
            last = cache["requests"][-1]
            print(
                f"Last request: {last['requested_at']} "
                f"(delta {last['timedelta_days']} days)"
            )
        else:
            print("No previous requests.")

        print("\nOptions:")
        print("  [1] Load last saved request (no API)")
        print("  [2] Make a new request")
        print("  [3] List all saved requests")
        print("  [4] View a specific request")
        print("  [q] Quit")

        choice = input("\nChoose: ").strip().lower()

        if choice == "q":
            print("Goodbye")
            break

        if choice == "1":
            if cache["requests"]:
                show_request(cache, cache["requests"][-1]["id"])
            else:
                print("No saved requests.")
            pause()
            continue

        if choice == "2":
            days = int(input("How many days back? "))
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

            print(f"\nSaved request {req['id']} ({len(vods)} VODs)")
            show_request(cache, req["id"])
            pause()
            continue

        if choice == "3":
            print_cache_summary(cache)
            pause()
            continue

        if choice == "4":
            req_id = int(input("Request ID: "))
            show_request(cache, req_id)
            pause()
            continue

        print("Invalid choice.")


if __name__ == "__main__":
    main()