#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("TRAKT_RADARR_CONFIG", os.path.join(BASE_DIR, "config.json"))
TOKEN_FILE = os.path.join(BASE_DIR, "trakt_tokens.json")
STATE_FILE = os.path.join(BASE_DIR, "sync_state.json")
LOCK_FILE = os.path.join(BASE_DIR, "SAFETY_LOCKED")
REQUEST_TIMEOUT = 60
session = requests.Session()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def atomic_json(path, data, mode=0o600):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"ERROR loading {CONFIG_FILE}: {exc}")
        sys.exit(1)


C = load_config()
T = C["trakt"]
R = C["radarr"]
S = C.get("sync", {})
SAFE = C.get("safety", {})
N = C.get("notifications", {})

CLIENT_ID = T["client_id"]
CLIENT_SECRET = T["client_secret"]
USERNAME = T["username"]
LIST_SLUG = T.get("list_slug", "my_watchlist")
LIST_NAME = T.get("list_name", "My_Watchlist")
RADARR_URL = R.get("url", "http://127.0.0.1:7878").rstrip("/")
API_KEY = R["api_key"]
QUALITY = R.get("quality_profile", "Drewy Quality")
ROOT = R.get("root_folder", "/mnt/movies")
TAG = S.get("managed_tag", "trakt-my-watchlist")
SEARCH = bool(S.get("search_on_add", True))
MONITORED = bool(S.get("monitored", True))
AVAIL = S.get("minimum_availability", "announced")
DELETE_REMOVED = bool(S.get("delete_removed_movies", True))
DELETE_FILES = bool(S.get("delete_files", True))
EXCLUDE = bool(S.get("add_import_exclusion", False))
PAGE_SIZE = min(int(S.get("trakt_page_size", 250)), 250)

MIN_EXPECTED = int(SAFE.get("minimum_expected_movies", 100))
MIN_RATIO = float(SAFE.get("minimum_list_percent_of_last_good", 80)) / 100.0
MAX_DELETE = int(SAFE.get("max_deletions_per_run", 10))
MAX_DELETE_PERCENT = float(SAFE.get("max_deletion_percent", 5)) / 100.0
REQUIRED_MISSING_RUNS = max(2, int(SAFE.get("require_missing_for_runs", 2)))
LOCK_ON_ANY_ERROR = bool(SAFE.get("lock_on_any_error", True))
DRY_RUN = bool(SAFE.get("dry_run", False))

NOTIFIARR_WEBHOOK = N.get("notifiarr_webhook_url", "").strip()


def save_tokens(d):
    x = {k: d[k] for k in ("access_token", "refresh_token")}
    x["created_at"] = d.get("created_at", int(time.time()))
    x["expires_in"] = d.get("expires_in", 604800)
    atomic_json(TOKEN_FILE, x)


def load_tokens():
    return load_json(TOKEN_FILE)


def expired(t):
    return time.time() >= t.get("created_at", 0) + t.get("expires_in", 604800) - 600


def refresh(rt):
    print("Refreshing Trakt authorization...")
    r = session.post(
        "https://auth.trakt.tv/oauth/token",
        json={
            "refresh_token": rt,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token",
        },
        timeout=REQUEST_TIMEOUT,
    )
    if r.status_code != 200:
        return None
    d = r.json()
    save_tokens(d)
    return d["access_token"]


def authorize():
    r = session.post(
        "https://api.trakt.tv/oauth/device/code",
        json={"client_id": CLIENT_ID},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    d = r.json()
    print(f"Open {d.get('verification_url', 'https://trakt.tv/activate')} and enter code: {d['user_code']}")
    start = time.time()
    interval = d["interval"]
    while time.time() - start < d["expires_in"]:
        time.sleep(interval)
        r = session.post(
            "https://api.trakt.tv/oauth/device/token",
            json={"code": d["device_code"], "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            x = r.json()
            save_tokens(x)
            print("Trakt authorization successful.")
            return x["access_token"]
        if r.status_code in (400, 404):
            continue
        if r.status_code == 418:
            interval += 5
            continue
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", interval)))
            continue
        r.raise_for_status()
    raise RuntimeError("Trakt authorization timed out")


def access_token():
    t = load_tokens()
    if not t:
        return authorize()
    if expired(t):
        return refresh(t.get("refresh_token")) or authorize()
    return t["access_token"]


def th(tok):
    return {
        "trakt-api-version": "2",
        "trakt-api-key": CLIENT_ID,
        "Authorization": f"Bearer {tok}",
        "User-Agent": "Trakt-Radarr-Sync/2.1",
    }


def rh():
    return {"X-Api-Key": API_KEY, "Content-Type": "application/json"}


def rr(method, path, **kw):
    return session.request(method, RADARR_URL + path, headers=rh(), timeout=REQUEST_TIMEOUT, **kw)


def notify_safety_lock(reason):
    # Optional direct webhook. Notifiarr webhook payload formats can vary by setup;
    # this intentionally sends a simple JSON payload and failure never weakens the lock.
    if not NOTIFIARR_WEBHOOK:
        return
    try:
        session.post(
            NOTIFIARR_WEBHOOK,
            json={
                "title": "Trakt-Radarr Sync SAFETY LOCK",
                "message": reason,
                "severity": "error",
                "timestamp": now_iso(),
            },
            timeout=15,
        )
    except Exception as exc:
        print(f"WARNING: notification webhook failed: {exc}")


def lock_sync(reason, details=None):
    state = load_json(STATE_FILE, {}) or {}
    lock_id = f"{int(time.time())}-{os.getpid()}"
    lock_data = {
        "locked": True,
        "lock_id": lock_id,
        "locked_at": now_iso(),
        "reason": reason,
        "details": details or {},
    }
    state["safety_lock"] = lock_data
    atomic_json(STATE_FILE, state)
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(lock_data, indent=2) + "\n")
    os.chmod(LOCK_FILE, 0o600)
    print("\n" + "!" * 68)
    print("SAFETY LOCK ENGAGED - NO FURTHER SYNCS WILL RUN")
    print(reason)
    print(f"Lock ID: {lock_id}")
    print(f"Acknowledge with: {sys.argv[0]} --acknowledge")
    print("!" * 68)
    notify_safety_lock(reason)
    raise SystemExit(2)


def is_locked():
    state = load_json(STATE_FILE, {}) or {}
    lock = state.get("safety_lock") or {}
    return bool(lock.get("locked")), lock


def acknowledge():
    state = load_json(STATE_FILE, {}) or {}
    lock = state.get("safety_lock") or {}
    if not lock.get("locked"):
        print("No safety lock is currently active.")
        return 0
    print("Current safety lock:")
    print(f"  Time:   {lock.get('locked_at')}")
    print(f"  Reason: {lock.get('reason')}")
    print(f"  ID:     {lock.get('lock_id')}")
    state["safety_lock"] = {
        "locked": False,
        "acknowledged_at": now_iso(),
        "previous_lock": lock,
    }
    # Rebaseline safely on next run; that run is never allowed to delete.
    state["rebaseline_next_run"] = True
    state["missing_counts"] = {}
    atomic_json(STATE_FILE, state)
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass
    print("Safety lock acknowledged and cleared.")
    print("The next sync will REBASELINE and will NOT delete any movies.")
    return 0


def get_trakt(tok):
    out = []
    page = 1
    url = f"https://api.trakt.tv/users/{quote(USERNAME, safe='')}/lists/{quote(LIST_SLUG, safe='')}/items/movies"
    expected_pages = None
    print(f"Reading Trakt list: {LIST_NAME}")
    while True:
        r = session.get(url, headers=th(tok), params={"page": page, "limit": PAGE_SIZE}, timeout=REQUEST_TIMEOUT)
        if r.status_code == 401:
            t = load_tokens()
            tok = refresh(t.get("refresh_token")) if t else None
            tok = tok or authorize()
            continue
        r.raise_for_status()
        items = r.json()
        if not isinstance(items, list):
            raise RuntimeError("Trakt returned malformed list data")
        pc = r.headers.get("X-Pagination-Page-Count")
        if pc:
            expected_pages = int(pc)
        print(f"  Page {page}: {len(items)} movies")
        if not items:
            break
        out.extend(items)
        if expected_pages is not None and page >= expected_pages:
            break
        if expected_pages is None and len(items) < PAGE_SIZE:
            break
        page += 1
    if expected_pages is not None and page != expected_pages:
        raise RuntimeError(f"Incomplete Trakt pagination: fetched through page {page}, expected {expected_pages}")
    return out


def profile_id():
    r = rr("GET", "/api/v3/qualityprofile")
    r.raise_for_status()
    for p in r.json():
        if p.get("name", "").lower() == QUALITY.lower():
            return p["id"]
    raise RuntimeError(f"Quality profile not found: {QUALITY}")


def tag_id():
    r = rr("GET", "/api/v3/tag")
    r.raise_for_status()
    for t in r.json():
        if t.get("label", "").lower() == TAG.lower():
            return t["id"]
    r = rr("POST", "/api/v3/tag", json={"label": TAG})
    r.raise_for_status()
    return r.json()["id"]


def movies():
    r = rr("GET", "/api/v3/movie")
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("Radarr returned malformed movie data")
    return data


def tag_movie(m, tid):
    if tid in m.get("tags", []):
        return False
    x = m.copy()
    x["tags"] = m.get("tags", []) + [tid]
    r = rr("PUT", f"/api/v3/movie/{m['id']}", json=x)
    r.raise_for_status()
    return True


def add_movie(tmdb, pid, tid):
    r = rr("GET", "/api/v3/movie/lookup/tmdb", params={"tmdbId": tmdb})
    r.raise_for_status()
    x = r.json()
    x.pop("id", None)
    x.update({
        "qualityProfileId": pid,
        "rootFolderPath": ROOT,
        "monitored": MONITORED,
        "minimumAvailability": AVAIL,
        "tags": [tid],
        "addOptions": {"searchForMovie": SEARCH},
    })
    r = rr("POST", "/api/v3/movie", json=x)
    r.raise_for_status()


def delete_movie(m):
    r = rr(
        "DELETE",
        f"/api/v3/movie/{m['id']}",
        params={
            "deleteFiles": str(DELETE_FILES).lower(),
            "addImportExclusion": str(EXCLUDE).lower(),
        },
    )
    r.raise_for_status()


def safety_validate(trakt_ids, managed_count, previous_ids, rebaseline):
    count = len(trakt_ids)
    if count == 0:
        lock_sync("Trakt returned ZERO movies. Deletion protection triggered.")
    if count < MIN_EXPECTED:
        lock_sync(
            f"Trakt returned only {count} movies, below minimum_expected_movies={MIN_EXPECTED}.",
            {"current_count": count, "minimum_expected": MIN_EXPECTED},
        )
    if previous_ids and not rebaseline:
        ratio = count / max(len(previous_ids), 1)
        if ratio < MIN_RATIO:
            lock_sync(
                f"Trakt list shrank from {len(previous_ids)} to {count} movies ({ratio*100:.1f}%), below the allowed {MIN_RATIO*100:.1f}% threshold.",
                {"previous_count": len(previous_ids), "current_count": count, "ratio": ratio},
            )
    if managed_count < 0:
        lock_sync("Internal managed movie count validation failed.")


def main():
    locked, lock = is_locked()
    if locked:
        print("SAFETY LOCK ACTIVE - sync refused.")
        print(f"Reason: {lock.get('reason')}")
        print(f"Locked: {lock.get('locked_at')}")
        print(f"Acknowledge with: {sys.argv[0]} --acknowledge")
        return 2

    print("=" * 68)
    print("Trakt -> Radarr Managed Sync (safety interlock enabled)")
    print("=" * 68)

    state = load_json(STATE_FILE, {}) or {}
    rebaseline = bool(state.get("rebaseline_next_run", False))

    try:
        tok = access_token()
        st = rr("GET", "/api/v3/system/status")
        st.raise_for_status()
        print("Radarr:", st.json().get("version"))
        pid = profile_id()
        tid = tag_id()
        trakt = get_trakt(tok)
        tmap = {i["movie"]["ids"]["tmdb"]: i["movie"] for i in trakt if i.get("movie", {}).get("ids", {}).get("tmdb")}
        trakt_ids = set(tmap)
        rmovies = movies()
        rmap = {m["tmdbId"]: m for m in rmovies if m.get("tmdbId")}
        managed = [m for m in rmovies if tid in m.get("tags", [])]
        previous_ids = set(state.get("last_good_trakt_ids", []))

        # ALL safety checks happen before any mutation.
        safety_validate(trakt_ids, len(managed), previous_ids, rebaseline)

    except SystemExit:
        raise
    except Exception as exc:
        if LOCK_ON_ANY_ERROR:
            lock_sync(f"Critical preflight error: {exc}")
        print(f"ERROR: {exc}")
        return 1

    print(f"Trakt movies: {len(trakt_ids)}")
    print(f"Managed Radarr movies: {len(managed)}")

    # First safe run / post-ack run: establish a trusted baseline and never delete.
    baseline_only = (not previous_ids) or rebaseline
    if baseline_only:
        print("SAFETY BASELINE MODE: deletions are disabled for this run.")

    # Tag existing list movies and add missing movies only after validation passed.
    try:
        for tmdb in set(tmap) & set(rmap):
            if tag_movie(rmap[tmdb], tid):
                print("TAGGED:", rmap[tmdb].get("title"))

        for tmdb in sorted(set(tmap) - set(rmap)):
            m = tmap[tmdb]
            print("ADDING:", m.get("title"), m.get("year"))
            if not DRY_RUN:
                add_movie(tmdb, pid, tid)
            print("  DRY RUN - WOULD ADD" if DRY_RUN else ("  ADDED + SEARCH TRIGGERED" if SEARCH else "  ADDED"))
            time.sleep(0.25)
    except Exception as exc:
        lock_sync(f"Radarr mutation error while adding/tagging: {exc}")

    # Re-read Radarr before calculating removals.
    try:
        current_movies = movies()
        managed = [m for m in current_movies if tid in m.get("tags", [])]
    except Exception as exc:
        lock_sync(f"Failed to re-read Radarr before deletion stage: {exc}")

    missing_counts = state.get("missing_counts", {}) if not baseline_only else {}
    new_missing_counts = {}
    eligible = []

    for m in managed:
        tmdb = m.get("tmdbId")
        if not tmdb or tmdb in trakt_ids:
            continue
        key = str(tmdb)
        count = int(missing_counts.get(key, 0)) + 1
        new_missing_counts[key] = count
        print(f"MISSING: {m.get('title')} ({m.get('year')}) confirmation {count}/{REQUIRED_MISSING_RUNS}")
        if count >= REQUIRED_MISSING_RUNS:
            eligible.append(m)

    # Absolute and percentage deletion circuit breakers.
    if eligible and not baseline_only:
        managed_count = max(len(managed), 1)
        pct = len(eligible) / managed_count
        if len(eligible) > MAX_DELETE:
            lock_sync(
                f"Proposed deletions={len(eligible)} exceeds max_deletions_per_run={MAX_DELETE}.",
                {"eligible": len(eligible), "max": MAX_DELETE},
            )
        if pct > MAX_DELETE_PERCENT:
            lock_sync(
                f"Proposed deletions are {pct*100:.1f}% of managed library, above max_deletion_percent={MAX_DELETE_PERCENT*100:.1f}%.",
                {"eligible": len(eligible), "managed": managed_count, "percent": pct * 100},
            )

    if DELETE_REMOVED and eligible and not baseline_only:
        for m in eligible:
            print("REMOVING:", m.get("title"), m.get("year"))
            try:
                if not DRY_RUN:
                    delete_movie(m)
                print("  DRY RUN - WOULD DELETE" if DRY_RUN else ("  DELETED FROM RADARR + FILES" if DELETE_FILES else "  REMOVED FROM RADARR"))
            except Exception as exc:
                lock_sync(f"Deletion failed for {m.get('title')}: {exc}")
            time.sleep(0.25)

    # Only a fully successful run becomes the new trusted state.
    state["last_good_trakt_ids"] = sorted(trakt_ids)
    state["last_good_count"] = len(trakt_ids)
    state["last_success_at"] = now_iso()
    state["missing_counts"] = new_missing_counts
    state["rebaseline_next_run"] = False
    state["safety_lock"] = {"locked": False}
    atomic_json(STATE_FILE, state)
    print("Sync complete. Trusted snapshot updated.")
    return 0


def parse_args():
    p = argparse.ArgumentParser(description="Safely sync a Trakt list to Radarr")
    p.add_argument("--acknowledge", action="store_true", help="Acknowledge and clear an active safety lock")
    p.add_argument("--status", action="store_true", help="Show safety lock/state status and exit")
    return p.parse_args()


def status():
    state = load_json(STATE_FILE, {}) or {}
    locked, lock = is_locked()
    print(f"Safety lock: {'ACTIVE' if locked else 'clear'}")
    if locked:
        print(f"Reason: {lock.get('reason')}")
        print(f"Locked at: {lock.get('locked_at')}")
        print(f"Lock ID: {lock.get('lock_id')}")
    print(f"Last successful sync: {state.get('last_success_at', 'never')}")
    print(f"Last good Trakt count: {state.get('last_good_count', 'none')}")
    print(f"Rebaseline next run: {state.get('rebaseline_next_run', False)}")
    return 0


if __name__ == "__main__":
    args = parse_args()
    if args.acknowledge:
        sys.exit(acknowledge())
    if args.status:
        sys.exit(status())
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted.")
        sys.exit(130)
