# Trakt → Radarr Sync

A Python utility that keeps a Trakt custom movie list synchronized with Radarr.

It was created as a workaround for Trakt's API pagination changes, which can prevent Radarr from retrieving more than the first 250 movies from a Trakt list.

The script handles Trakt pagination itself and communicates directly with the Radarr API.

## Features

- Retrieves **all movies** from a Trakt custom list
- Handles Trakt's **250-item pagination limit**
- Automatically adds missing movies to Radarr
- Automatically triggers a Radarr search for newly added movies
- Uses your configured Radarr quality profile and root folder
- Automatically creates a dedicated Radarr management tag
- Tags movies managed by the Trakt list
- Optionally removes movies from Radarr when they are removed from Trakt
- Can also delete the corresponding movie files
- Leaves unrelated Radarr movies untouched
- Trakt OAuth device authentication
- Automatic Trakt access-token refresh
- Persistent safety interlock
- Two-run deletion confirmation
- Last-known-good Trakt snapshot
- Automatic detection of suspicious list changes
- Maximum deletion limits
- Automatic retries for temporary Trakt/API failures
- Designed to run unattended using a systemd timer

---

# How It Works

Movies in the configured Trakt list are compared using their TMDb IDs against the movies currently present in Radarr.

When a movie is added to Trakt:

```text
Trakt list
    ↓
Movie detected by sync
    ↓
Added to Radarr
    ↓
Management tag applied
    ↓
Radarr search triggered
```

When a managed movie is removed from Trakt:

```text
Movie missing from Trakt
    ↓
First successful sync
    ↓
Marked as missing — NO deletion
    ↓
Second consecutive successful sync
    ↓
Safety checks performed
    ↓
Movie removed from Radarr
    ↓
Movie files optionally deleted
```

Only movies carrying the script's management tag are eligible for automatic deletion.

Movies that were never managed by the script are left untouched.

---

# Safety System

Because this script can optionally remove movies and their files, deletion is protected by several independent safeguards.

The script is designed to **fail closed**.

If it cannot confidently establish the current state of the Trakt list, no destructive action is performed.

## Management Tag

By default, the script creates:

```text
trakt-my-watchlist
```

Movies managed by the script receive this tag in Radarr.

A movie can only be automatically removed if it has this tag.

This prevents unrelated Radarr movies from being deleted.

---

# Two-Run Deletion Confirmation

A movie must be absent from Trakt for multiple consecutive successful syncs before deletion is permitted.

The default is:

```text
REQUIRE_MISSING_FOR_RUNS = 2
```

Example:

```text
15:00
Movie missing from Trakt
Missing confirmation: 1/2
NO deletion

15:05
Movie still missing
Missing confirmation: 2/2
Movie eligible for deletion
```

If the movie reappears before the required number of confirmations is reached, its missing counter is reset.

This protects against temporary or incomplete Trakt responses.

---

# Last-Known-Good Snapshot

The script stores the TMDb IDs returned by the last trusted Trakt sync.

This provides a baseline against which subsequent results can be checked.

A suspicious or failed sync does **not** replace the trusted snapshot.

This prevents a bad API response from becoming the new baseline.

---

# Safety Lock

Dangerous or suspicious conditions cause the script to engage a persistent safety lock.

Example:

```text
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
SAFETY LOCK ENGAGED - NO FURTHER SYNCS WILL RUN

Reason: Current Trakt movie count dropped below the configured
safety threshold.

NO MOVIES WERE DELETED.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

Once locked, subsequent scheduled runs refuse to perform synchronization.

The lock remains active across service restarts and LXC/server reboots.

## Check Lock Status

Run:

```bash
/opt/trakt-radarr-sync/sync_trakt_radarr.py --status
```

## Acknowledge a Safety Lock

After investigating the reason for the lock:

```bash
/opt/trakt-radarr-sync/sync_trakt_radarr.py --acknowledge
```

The lock will be cleared.

However, **deletion is not immediately re-enabled**.

The next successful run is forced into baseline/recovery mode and cannot delete movies.

This establishes a new trusted checkpoint before normal deletion processing resumes.

---

# Conditions That Can Trigger a Safety Lock

Examples include:

- Trakt unexpectedly returning zero movies
- The Trakt movie list becoming suspiciously smaller than the last trusted result
- More movies being proposed for deletion than the configured maximum
- The percentage of managed movies proposed for deletion exceeding the configured threshold
- Invalid or malformed data that makes the list unsafe to evaluate
- Other conditions where the script cannot safely determine which movies should be removed

The script will not deliberately proceed with destructive operations after a safety lock has been engaged.

---

# Temporary Trakt Errors

Temporary API/server failures are handled differently from safety violations.

The following HTTP responses are treated as potentially temporary:

```text
429 Too Many Requests
500 Internal Server Error
502 Bad Gateway
503 Service Unavailable
504 Gateway Timeout
```

Network connection failures and request timeouts are also treated as temporary.

The script retries these failures automatically.

The retry delays are:

```text
5 seconds
15 seconds
30 seconds
```

If Trakt recovers, synchronization continues normally.

## If All Retries Fail

If Trakt is still unavailable after all retry attempts, the current synchronization is aborted:

```text
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
SYNC ABORTED - NO CHANGES MADE

Temporary Trakt failure:
Trakt returned HTTP 500 after all retry attempts.

No safety lock was created.
The next scheduled run may try again normally.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

No movies are added or deleted during the failed run.

Importantly, a temporary Trakt outage **does not engage the persistent safety lock**.

If the script is running every five minutes, the next scheduled run simply tries again.

This prevents temporary Trakt outages from requiring manual acknowledgement.

---

# Safety Lock vs Temporary Failure

There are two deliberately different failure modes.

## Temporary Failure

Example:

```text
SYNC ABORTED - NO CHANGES MADE
```

This normally indicates a temporary network or Trakt server problem.

Behaviour:

```text
No Radarr changes
No deletion
No persistent lock
Next scheduled run tries again
No acknowledgement required
```

## Safety Violation

Example:

```text
SAFETY LOCK ENGAGED - NO FURTHER SYNCS WILL RUN
```

This means the script received data that could make automatic deletion unsafe.

Behaviour:

```text
No deletion
Persistent lock created
Future syncs refused
Manual investigation required
Acknowledgement required
Next successful run is baseline-only
```

---

# Pagination

Trakt paginated API endpoints currently have a maximum page size of 250 items.

The script explicitly requests:

```text
?page=1&limit=250
?page=2&limit=250
?page=3&limit=250
...
```

until the complete movie list has been retrieved.

The script uses pagination state rather than assuming that the number of movie results must equal Trakt's broader list item count.

This is important for lists that may contain multiple media types.

---

# Requirements

- Python 3
- `requests`
- Radarr
- Trakt API application
- Network access to Radarr and Trakt

For Debian/Ubuntu:

```bash
apt update
apt install -y python3 python3-requests
```

No `pip` installation is required.

---

# Installation

Create the application directory:

```bash
mkdir -p /opt/trakt-radarr-sync
```

Copy the repository files into:

```text
/opt/trakt-radarr-sync/
```

Make the script executable:

```bash
chmod +x /opt/trakt-radarr-sync/sync_trakt_radarr.py
```

---

# Configuration

Copy the example configuration:

```bash
cp config.example.json config.json
```

Edit it:

```bash
nano config.json
```

Enter your Trakt and Radarr details.

Do **not** commit `config.json` to GitHub.

The supplied `.gitignore` excludes sensitive local configuration and OAuth token files.

---

# Trakt API Application

Create a Trakt API application and obtain:

```text
Client ID
Client Secret
```

The script uses Trakt's OAuth Device Code flow.

On the first run, it will display an activation URL and code.

Example:

```text
Trakt authorization required

Open this address in a browser:

https://trakt.tv/activate

Enter this code:

AB12CD34

Waiting for authorization...
```

After successful authorization, the OAuth tokens are stored locally.

Subsequent runs use the saved token.

When the access token approaches expiry, the script automatically uses the refresh token to obtain new credentials and stores the replacement tokens.

---

# Radarr Configuration

The script requires:

- Radarr URL
- Radarr API key
- Quality profile
- Root folder

The API key can be found under:

```text
Settings
→ General
→ Security
→ API Key
```

Example Radarr URL when running the script inside the same Proxmox LXC:

```text
http://127.0.0.1:7878
```

---

# Search on Add

New movies can automatically trigger a Radarr search.

Enable:

```text
SEARCH_ON_ADD = True
```

When a missing movie is added:

```text
Movie added to Radarr
    ↓
Radarr search triggered
```

Existing movies are not repeatedly searched on every synchronization.

---

# Automatic Deletion

Automatic deletion can be controlled independently.

Example:

```text
ENABLE_DELETIONS = True
DELETE_FILES = True
```

With:

```text
DELETE_FILES = True
```

the Radarr entry **and downloaded movie files** are removed.

With:

```text
DELETE_FILES = False
```

only the Radarr movie entry is removed.

Because deletion can remove actual media files, review the safety settings before enabling it.

---

# Deletion Limits

The script can refuse unusually large deletion batches.

Typical settings include:

```text
MAX_DELETIONS_PER_RUN = 10
MAX_DELETION_PERCENT = 5
```

For example, if 47 movies suddenly appear eligible for deletion:

```text
Movies proposed for deletion: 47
Maximum permitted per run:    10

SAFETY LOCK ENGAGED
NO MOVIES WERE DELETED.
```

This protects against corrupted, incomplete or unexpectedly changed Trakt responses.

---

# Running Manually

Run:

```bash
python3 /opt/trakt-radarr-sync/sync_trakt_radarr.py
```

or:

```bash
/opt/trakt-radarr-sync/sync_trakt_radarr.py
```

---

# Running Every Five Minutes

A systemd service and timer are included.

Copy them:

```bash
cp systemd/trakt-radarr-sync.service /etc/systemd/system/
cp systemd/trakt-radarr-sync.timer /etc/systemd/system/
```

Reload systemd:

```bash
systemctl daemon-reload
```

Enable and start the timer:

```bash
systemctl enable --now trakt-radarr-sync.timer
```

Check it:

```bash
systemctl status trakt-radarr-sync.timer
```

View the next scheduled execution:

```bash
systemctl list-timers trakt-radarr-sync.timer
```

---

# Logs

View recent sync output:

```bash
journalctl -u trakt-radarr-sync.service -n 100 --no-pager
```

Follow the log live:

```bash
journalctl -u trakt-radarr-sync.service -f
```

For troubleshooting, running the script manually is often useful:

```bash
/opt/trakt-radarr-sync/sync_trakt_radarr.py
```

---

# Updating

When replacing the Python script with a newer version:

```bash
cp sync_trakt_radarr.py /opt/trakt-radarr-sync/sync_trakt_radarr.py
chmod +x /opt/trakt-radarr-sync/sync_trakt_radarr.py
```

The existing systemd timer does not need to be recreated unless the service/timer files themselves have changed.

---

# GitHub Security

Never commit:

```text
config.json
trakt_tokens.json
```

or any file containing:

- Trakt Client Secret
- Trakt OAuth access token
- Trakt refresh token
- Radarr API key

These files should remain excluded by `.gitignore`.

If a secret is accidentally committed to a public repository, treat it as compromised and replace/revoke it rather than simply deleting it from the latest commit.

---

# Recommended Safety Settings

For an unattended installation, the recommended approach is:

```text
Management tag enabled
Two-run deletion confirmation enabled
Last-known-good snapshot enabled
Minimum list-size sanity checks enabled
Maximum deletions per run enabled
Maximum deletion percentage enabled
Persistent safety lock enabled
Baseline-only run after acknowledgement enabled
Temporary Trakt retry handling enabled
```

These protections are intentionally conservative because the script may have permission to delete actual movie files.

---

# Troubleshooting

## `SAFETY LOCK ACTIVE - sync refused`

Check the reason:

```bash
/opt/trakt-radarr-sync/sync_trakt_radarr.py --status
```

Investigate the reported condition before acknowledging it.

Then:

```bash
/opt/trakt-radarr-sync/sync_trakt_radarr.py --acknowledge
```

The next successful run will establish a new baseline without deleting anything.

## `SYNC ABORTED - NO CHANGES MADE`

This normally means Trakt or the network remained unavailable after automatic retries.

No acknowledgement is required.

Wait for the next systemd run or try manually:

```bash
/opt/trakt-radarr-sync/sync_trakt_radarr.py
```

## Trakt HTTP 500/502/503/504

These are treated as temporary failures.

The script automatically retries before aborting the current run.

They do not by themselves create a persistent safety lock.

## Trakt HTTP 429

The script treats API rate limiting as temporary and retries rather than performing an unsafe partial synchronization.

## Radarr Connection Error

Check:

```bash
curl http://127.0.0.1:7878
```

and verify the configured Radarr URL and API key.

## View Service Errors

```bash
journalctl -u trakt-radarr-sync.service -n 200 --no-pager
```

---

# Disclaimer

This software can optionally remove movies and delete media files from Radarr.

Test it carefully before enabling automatic deletion.

Backups are strongly recommended.

The safety mechanisms are designed to reduce the possibility of unintended deletion, but no software safeguard can guarantee against every possible failure or configuration error.

Use at your own risk.

---

# License

MIT License.
