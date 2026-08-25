# Trakt Radarr Sync

Synchronise a Trakt custom movie list with Radarr without being limited to the first 250 Trakt results.

## Features

- Fetches all pages from a Trakt custom list.
- Trakt device OAuth on first run and automatic token refresh afterwards.
- Adds missing movies to Radarr.
- Optionally triggers an immediate Radarr search when a movie is added.
- Creates and applies a dedicated `trakt-my-watchlist` tag.
- Optionally removes movies from Radarr when they are removed from Trakt.
- Deletion is restricted to movies carrying the managed tag, so unrelated Radarr movies are left alone.
- Optional deletion of the movie files themselves.
- Includes a systemd timer for syncing every five minutes.

## Requirements

- Python 3
- `requests`
- A Trakt API application with Client ID and Client Secret
- Radarr API key

On Debian/Ubuntu:

```bash
apt update
apt install -y python3 python3-requests
```

## Installation

```bash
mkdir -p /opt/trakt-radarr-sync
cp sync_trakt_radarr.py /opt/trakt-radarr-sync/
cp config.example.json /opt/trakt-radarr-sync/config.json
chmod +x /opt/trakt-radarr-sync/sync_trakt_radarr.py
chmod 600 /opt/trakt-radarr-sync/config.json
```

Edit `/opt/trakt-radarr-sync/config.json` and enter your Trakt Client ID/Secret, Trakt username, Radarr API key, quality profile and root folder.

If the script runs inside the Radarr LXC, `http://127.0.0.1:7878` is normally suitable for the Radarr URL.

## First run

```bash
python3 /opt/trakt-radarr-sync/sync_trakt_radarr.py
```

On the first run the script displays a Trakt activation URL and code. Authorise it once. OAuth tokens are saved to `trakt_tokens.json` and refreshed automatically.

## Deletion warning

The example configuration contains:

```json
"delete_removed_movies": true,
"delete_files": true
```

With both enabled, a movie managed by this script that is subsequently removed from the Trakt list is removed from Radarr **and its movie files are deleted**.

For a safer initial test, set `delete_files` to `false`.

Only movies carrying the configured managed tag are candidates for deletion.

## systemd timer

Install the included units:

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

View sync logs:

```bash
journalctl -u trakt-radarr-sync.service -n 100 --no-pager
```

The supplied timer runs one minute after boot and then every five minutes.

## Updating configuration

The main settings are in `config.json`; credentials do not need to be placed in the Python source. `config.json` and `trakt_tokens.json` are excluded by `.gitignore` and should never be committed to a public repository.

## License

MIT
