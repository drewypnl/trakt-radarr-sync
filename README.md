# Trakt Radarr Sync

Safely sync a Trakt custom movie list (for example `My_Watchlist`) to Radarr, including lists larger than Trakt's per-page limit.

## Features

- Full Trakt pagination
- Pagination completeness is validated by traversing Trakt pages, not by comparing the movie count to Trakt's broader list item-count header
- OAuth device authorization with automatic token refresh
- Adds missing movies to Radarr
- Triggers a Radarr search when a movie is added
- Tags managed movies with `trakt-my-watchlist`
- Optionally removes movies/files after they are removed from Trakt
- **Fail-closed deletion safeguards**
- Persistent safety lock that survives service restarts/reboots
- Manual acknowledgement required after a safety lock
- Last-known-good Trakt snapshot
- Two-consecutive-run removal confirmation
- Absolute and percentage deletion circuit breakers
- First run after install/acknowledgement is baseline-only: **no deletions**
- Optional generic webhook notification for a safety lock


## Trakt mixed-list pagination note

Trakt custom lists can contain more than one media type. This sync requests only `/items/movies`. Trakt's `X-Pagination-Item-Count` header can describe the broader list rather than the filtered movie-only result, so it must **not** be used as an exact movie-count checksum.

The script therefore validates pagination by following the advertised page count (or, when no page count is supplied, by continuing until a short final page). The separate safety checks still protect against zero results, unexpectedly small lists, sudden drops relative to the last trusted snapshot, and excessive proposed deletions.

## Important safety behaviour

The script performs the complete Trakt fetch and Radarr inventory before it changes anything. Deletion is blocked if any configured safety check fails.

Default safeguards:

- Trakt returning 0 items => safety lock
- Fewer than 100 Trakt movies => safety lock
- Current list below 80% of the last trusted list => safety lock
- More than 10 proposed deletions => safety lock
- More than 5% of managed Radarr movies proposed for deletion => safety lock
- A movie must be absent for 2 consecutive successful runs before deletion
- Any critical API/preflight/mutation error => safety lock
- A locked script refuses all future scheduled runs until manually acknowledged

Only movies carrying the configured `trakt-my-watchlist` tag can ever be considered for removal.

## Install

```bash
mkdir -p /opt/trakt-radarr-sync
cp sync_trakt_radarr.py /opt/trakt-radarr-sync/
cp config.example.json /opt/trakt-radarr-sync/config.json
chmod +x /opt/trakt-radarr-sync/sync_trakt_radarr.py
chmod 600 /opt/trakt-radarr-sync/config.json
apt update
apt install -y python3-requests
```

Edit:

```bash
nano /opt/trakt-radarr-sync/config.json
```

Add your Trakt client ID/secret and Radarr API key.

## First run

```bash
python3 /opt/trakt-radarr-sync/sync_trakt_radarr.py
```

The first run establishes a trusted baseline and **does not delete anything**.

## Safety lock

If a dangerous or unexpected condition is detected, the script creates:

```text
/opt/trakt-radarr-sync/SAFETY_LOCKED
```

and records the details in `sync_state.json`. Every future timer run exits immediately without syncing.

Check status:

```bash
python3 /opt/trakt-radarr-sync/sync_trakt_radarr.py --status
```

Acknowledge after investigating:

```bash
python3 /opt/trakt-radarr-sync/sync_trakt_radarr.py --acknowledge
```

The next run after acknowledgement is automatically a **rebaseline run with deletion disabled**. This prevents acknowledgement itself from immediately permitting a mass deletion.

## Safety configuration

```json
"safety": {
  "minimum_expected_movies": 100,
  "minimum_list_percent_of_last_good": 80,
  "max_deletions_per_run": 10,
  "max_deletion_percent": 5,
  "require_missing_for_runs": 2,
  "lock_on_any_error": true,
  "dry_run": false
}
```

If you intentionally make a very large change to the Trakt list, the circuit breaker may lock. Investigate, acknowledge the lock, and adjust the thresholds only if the change is genuinely intended.

## Radarr health / Notifiarr note

Radarr does not expose a supported API that lets an external script inject a custom Radarr Health warning/error. The safety lock therefore lives in this sync service rather than fabricating or modifying Radarr's database.

The service exits non-zero when locked, so the failure is visible in systemd/journal. An optional webhook URL can also be configured:

```json
"notifications": {
  "notifiarr_webhook_url": ""
}
```

Because Notifiarr webhook configuration varies, leave this blank unless you have a webhook endpoint that accepts a simple JSON message. A notification failure never clears or weakens the safety lock.

## systemd timer (every 5 minutes)

```bash
cp systemd/trakt-radarr-sync.service /etc/systemd/system/
cp systemd/trakt-radarr-sync.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now trakt-radarr-sync.timer
```

Check the timer:

```bash
systemctl list-timers trakt-radarr-sync.timer
```

View the latest run:

```bash
journalctl -u trakt-radarr-sync.service -n 100 --no-pager
```

## Test without changing Radarr

Set:

```json
"dry_run": true
```

in the `safety` section.
