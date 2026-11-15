# Deployment guide — PoriaLinkBot

This is an operator handoff, **not a claim that deployment has occurred**.
Examples use synthetic hostnames, service paths, and placeholders. Replace them
before running anything. No live Telegram calls or server changes were performed
while preparing this repository.

## 1. Platform and planning

Use a maintained Linux distribution with systemd, persistent local storage,
Python with SQLite support, an HTTPS reverse proxy, and outbound Telegram access.
CPython **3.14.4** is the release-tested runtime; dependency metadata permits
3.10+, but other interpreters require their own full test run. Obtain Python from
a trusted distribution/source and verify `python3.14 --version` before installing.

A planning baseline is one vCPU, 1 GiB RAM, and sufficient persistent disk for the
OS, database, events, and backups. This is not a benchmark or capacity guarantee.
Load-test your workload. SQLite is single-host storage with serialized writers;
use one polling bot instance per token. Do not share the database over a network
filesystem or scale the bot horizontally without redesigning flow state.

Expose only HTTPS (and HTTP for HTTPS redirection/ACME), plus tightly restricted
administrative server access. Keep the WSGI port on loopback. Telegram uses
outbound long polling; no public Telegram webhook/admin/report endpoint is needed.

## 2. Reviewed release installation

The commands below are examples for an administrator on a new server. Confirm
ownership/publication rights and the release gate before cloning. Do not clone
local databases or secrets. Keep code immutable to the runtime account.

```bash
sudo useradd --system --user-group --home-dir /var/lib/porialink --shell /usr/sbin/nologin porialink
sudo install -d -o root -g root -m 755 /opt/porialink/releases
sudo install -d -o porialink -g porialink -m 700 /var/lib/porialink
sudo install -d -o root -g root -m 700 /etc/porialink
```

Choose a published, reviewed commit/tag and a new directory. These placeholders
must be replaced; they are not current repository or release claims:

```bash
REPO_URL='https://github.com/OWNER/REPOSITORY.git'
RELEASE_REF='replace-with-reviewed-commit-or-tag'
RELEASE_DIR='/opt/porialink/releases/release-001'
sudo git clone "$REPO_URL" "$RELEASE_DIR"
sudo git -C "$RELEASE_DIR" checkout --detach "$RELEASE_REF"
sudo python3.14 -m venv "$RELEASE_DIR/.venv"
sudo "$RELEASE_DIR/.venv/bin/python" -m pip install -r "$RELEASE_DIR/requirements.lock"
sudo "$RELEASE_DIR/.venv/bin/python" -m pip check
```

The lock pins application dependencies from the tested environment. Independently
perform a clean installation, advisory audit, and full suite on this server's
interpreter/platform. Do not claim deployment compatibility from `pip check`
alone. Use trusted package indexes; never embed index/proxy credentials in Git.

For WSGI, select and audit a compatible Gunicorn version, then pin it in the
infrastructure's deployment lock. Gunicorn is intentionally not a dependency of
the local bot/Flask development runtime:

```bash
GUNICORN_VERSION='replace-with-reviewed-version'
sudo "$RELEASE_DIR/.venv/bin/python" -m pip install "gunicorn==$GUNICORN_VERSION"
sudo "$RELEASE_DIR/.venv/bin/python" -m pip check
```

Do not copy this placeholder as a real version. Record the selected version and
resolved infrastructure dependencies before launch. Test a new release directory
before making it current. Create `/opt/porialink/current` as a symlink to the
reviewed directory using a controlled atomic switch; never edit live code in place.
For a first install, or after stopping both services during an update:

```bash
sudo ln -s "$RELEASE_DIR" /opt/porialink/current.next &&
sudo mv -T /opt/porialink/current.next /opt/porialink/current
```

Inspect an existing `current.next` before retrying; do not overwrite a normal
directory or remove the prior release. These examples assume GNU tools on Linux.

## 3. Protected environment files

Python does **not** automatically load `.env`. Local shells may explicitly source
trusted files; systemd below uses `EnvironmentFile`. Do not put `export` prefixes
or shell substitutions in systemd environment files. Shell and systemd formats
are not interchangeable for arbitrary complex secrets.

Create `/etc/porialink/web.env` with root-only permissions (`0600`):

```ini
APP_ENV=production
BASE_URL=https://links.example.com
DATABASE_PATH=/var/lib/porialink/url_shortener.db
FLASK_DEBUG=0
```

Create `/etc/porialink/bot.env` with the same four settings plus privately
supplied token and numeric administrator IDs. Never commit the completed file:

```ini
APP_ENV=production
BASE_URL=https://links.example.com
DATABASE_PATH=/var/lib/porialink/url_shortener.db
FLASK_DEBUG=0
TELEGRAM_BOT_TOKEN=replace-with-your-own-token
TELEGRAM_ADMIN_IDS=
```

The empty allowlist grants nobody access. Populate it with the actual authorized
numeric IDs, separated by commas, using a protected editor/secret-management
process. Do not place tokens/IDs in command-line arguments, support tickets, or
screenshots. Obtain the token for the intended bot through Telegram's official
bot-management workflow; the code never changes the Telegram username.

Production startup requires an explicit public **HTTPS origin** with no userinfo,
path, query, or fragment, and an absolute nonempty persistent database path.
Default ports/hostname casing normalize. Private/local origins and truthy
`FLASK_DEBUG` are rejected. Validation is lexical; verify public DNS, routing,
certificate validity, and HTTPS separately. Explicitly set `APP_ENV=production`:
the code cannot detect an operator accidentally using development mode.

The two services below share one trusted OS account for a simple single-host
setup. That is not strong process/credential isolation; use separate identities
with a deliberately designed shared-database permission model if required by an
organization. Keep bot credentials out of the web environment regardless.

### Proxy configuration

HTTPX honors `HTTPS_PROXY`, `ALL_PROXY`, `HTTP_PROXY`, and `NO_PROXY`. Telegram
HTTPS requests need HTTPS_PROXY or ALL_PROXY; HTTP_PROXY alone applies to HTTP.
An HTTP CONNECT proxy normally has an `http://` URL even for HTTPS destinations.
Both API and polling clients use 30-second connect/read settings. Remove leftover
workstation/WSL proxy settings unless deliberately required. In a container,
loopback is the container itself. Never log credentials embedded in proxy URLs.

## 4. Database initialization and private validation

Load the common environment only in a protected administrator shell. Run
initialization as the service account, not as root, so the database is writable
by the running services. One way to use the root-owned environment file without
making it readable to the service account is a transient systemd unit:

```bash
sudo systemd-run --wait --pipe --collect \
  --unit=porialink-init \
  --property=User=porialink --property=Group=porialink \
  --property=UMask=0077 \
  --property=WorkingDirectory=/opt/porialink/current \
  --property=EnvironmentFile=/etc/porialink/web.env \
  /opt/porialink/current/.venv/bin/python database.py
```

Review transient-unit support on the target systemd version. Initialization is
explicit and idempotent for the current schema; it is not an automatic general
migration system. Back up existing databases before initialization/upgrades and
audit any legacy missing columns, collisions, or malformed expiration rows.

Verify the expected file exists with service-account ownership, and privately
check `PRAGMA quick_check`, `PRAGMA foreign_key_check`, and the users/links/
click_events schema. Do not print user rows into shared logs. A misspelled path
can create an empty database and produce missing-table errors. Both processes
must use the identical absolute path. Keep the database directory writable for
SQLite journals, private, persistent, and outside all web roots.

## 5. WSGI and systemd services

Use a production WSGI server; never expose Flask's development server/debugger.
The application entry point is `app:app`. Start conservatively with one worker
and assess actual SQLite contention before changing worker/thread counts.

Create `/etc/porialink/gunicorn.conf.py` (root-owned):

```python
bind = "127.0.0.1:8000"
workers = 1
timeout = 60
accesslog = None
errorlog = "-"
loglevel = "warning"
forwarded_allow_ips = "127.0.0.1"
```

Do not enable request/body/header logging. Review the selected Gunicorn version's
logging behavior; startup infrastructure errors may include local paths. Keep
journals restricted. Do not put credentials in Gunicorn arguments/configuration.

`/etc/systemd/system/porialink-web.service`:

```ini
[Unit]
Description=PoriaLinkBot redirect server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=porialink
Group=porialink
WorkingDirectory=/opt/porialink/current
EnvironmentFile=/etc/porialink/web.env
ExecStart=/opt/porialink/current/.venv/bin/gunicorn --config /etc/porialink/gunicorn.conf.py app:app
Restart=on-failure
RestartSec=10
TimeoutStopSec=60
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/porialink

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/porialink-bot.service`:

```ini
[Unit]
Description=PoriaLinkBot Telegram polling service
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=porialink
Group=porialink
WorkingDirectory=/opt/porialink/current
EnvironmentFile=/etc/porialink/bot.env
ExecStart=/opt/porialink/current/.venv/bin/python bot.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=60
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/porialink

[Install]
WantedBy=multi-user.target
```

The bot's private temporary directory supports CSV spooling/cleanup. Neither
service requires root privileges or write access to source code. Verify these
hardening options on the selected Linux distribution. No application service
files have been installed by this repository preparation.

After review, environment setup, database initialization, and TLS preparation:

```bash
sudo systemd-analyze verify /etc/systemd/system/porialink-web.service /etc/systemd/system/porialink-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now porialink-web.service porialink-bot.service
sudo systemctl is-active porialink-web.service porialink-bot.service
```

Only one polling instance may use a token. Pending expiration/export flows are
not persisted; users restart them after a bot restart. Do not enable concurrent
updates without synchronization. Repeated startup failures should alert an
operator, not restart indefinitely without investigation.

## 6. Nginx and TLS

Install Nginx using the distribution's maintained packages. Point your hostname's
DNS to the server. Issue a certificate through your reviewed ACME/certificate
process before enabling a TLS server block with certificate paths. If using
Certbot, follow its official instructions for the target OS; do not copy a
certificate private key into this repository.

A webroot ACME bootstrap can use an HTTP-only server with
`location /.well-known/acme-challenge/ { root /var/lib/letsencrypt; }`. Create that
public challenge directory separately from the private application data, obtain
the certificate, then enable the HTTPS block below. Validate the HTTP challenge
path before issuance and configure automatic renewal and Nginx reload afterward.

Illustrative Nginx virtual host (replace hostname and certificate paths):

```nginx
server {
    listen 80;
    server_name links.example.com;
    access_log off;
    error_log /dev/null;
    location /.well-known/acme-challenge/ {
        root /var/lib/letsencrypt;
    }
    location / {
        return 308 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name links.example.com;
    ssl_certificate /etc/letsencrypt/live/links.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/links.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    access_log off;
    error_log /dev/null;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For "";
        proxy_set_header X-Real-IP "";
        proxy_buffering on;
        proxy_cache off;
        proxy_next_upstream off;
        proxy_intercept_errors off;
        add_header Cache-Control "no-store" always;
    }
}
```

This example discards request-level Nginx logs for privacy. It trades diagnostic
detail for privacy; use aggregate health/status metrics and controlled offline
reproduction. Configure a default virtual host to reject unexpected Host names
and inspect inherited/global logging and cache settings. Do not add aliases for
the repository, `.env`, database, backups, reports, or logs. Public endpoints do
not serve files from the project directory. Automatic upstream retries/caching
are disabled because redirect requests have click-recording side effects.

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Enable/verify certificate renewal with the chosen certificate tooling. Restrict
external access to the loopback WSGI port. Add deployment-level abuse/rate controls
and alerting before public launch; this example does not implement them.

## 7. Health and deployment verification

`GET /` is liveness only, returning a fixed 200 message. It does not validate the
database, Telegram access, DNS, or event persistence. During deployment, privately
check process state, database readability/schema/integrity, available disk,
backup freshness, TLS expiry, and bounded error/restart rates.

After configuration is approved, an operator can run these HTTP checks; they
have not been executed during repository preparation:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/
curl --fail --silent --show-error https://links.example.com/
```

Use synthetic data for a controlled Telegram smoke test: `/start`, URL submission,
expiration selection/custom dates, duplicate reuse, `/cancel`, and `/mylinks`.
Verify administrator commands/callbacks fail for an ordinary account and groups.
Check `/admin`, reports, and `/export` privately with a configured administrator.
Download a BOM-prefixed CSV; confirm counts/UTC dates, formula safety, and truncation
notices using synthetic data. Verify eligible redirects are 302, missing 404,
unavailable 410, and controlled database write failures 503 without partial events.
Do not fault-inject into a live real-user database.

## 8. Backup and restore

Set a protected backup destination outside web roots and source directories.
Use SQLite's online backup API rather than copying a live database file. Run a
reviewed backup script as an account with appropriate source/destination access;
use a restrictive umask and a new, distinct destination for each backup:

```python
import os
import sqlite3
from contextlib import closing
from pathlib import Path

os.umask(0o077)
source_path = Path(os.environ["DATABASE_PATH"]).resolve()
backup_path = Path(os.environ["BACKUP_PATH"]).resolve()
if source_path == backup_path or backup_path.exists():
    raise ValueError("Choose a new, distinct backup destination")
with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source, \
     closing(sqlite3.connect(str(backup_path))) as target:
    source.backup(target, pages=256)
    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise RuntimeError("Backup integrity check failed")
```

`BACKUP_PATH` is an operator backup-script variable, not an application setting.
Schedule backups with reviewed systemd timers/cron, monitor completion and storage,
apply an explicit retention schedule, encrypt copies appropriately, and periodically
restore to an isolated test path. Never print user rows or copy production data
into a portfolio checkout. Verify restored schema, foreign keys, counts, and
synthetic redirect behavior before relying on the backup.

For a real restore, stop both application services, preserve the current database
and journal/WAL/SHM state as a controlled snapshot, restore a verified offline
backup to the configured path with correct ownership/permissions, then restart.
Do not replace an open database or mix stale journal sidecars with a restored DB.
Restore procedures should be rehearsed on a separate instance first.

## 9. Monitoring, updates, and rollback

Application logs use fixed categories without exception payloads, sender IDs,
tokens, private URLs, or report contents. Keep HTTPX/Telegram/Werkzeug logging
suppressed; inspect systemd/WSGI/proxy logs separately. Never enable verbose
request/SQL/header/body logging in production. Restrict and rotate journals.
Monitor aggregate 5xx rates, SQLite busy/disk errors, bot restarts/polling state,
backup age, disk growth, and certificate expiry without retaining request payloads.

Before updating dependencies, review upstream advisories, resolve/pin runtime and
infrastructure packages in a separate clean environment, run `pip check`, run a
network-backed vulnerability audit, and execute the complete offline test suite.
Do not silently regenerate pins on a live server. Keep the prior immutable release
and infrastructure lock for rollback. Live vulnerability/clean-install checks
remain release warnings until executed; see [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

Rollback sequence: stop both processes, assess whether the database schema/data
is compatible with the prior release, switch the current symlink back to the
previous verified directory, restore matching dependencies/configuration, and
restart/verify. Do not restore an older database just to revert code: it loses
new writes. If a database restore is necessary, obtain approval for that loss,
use a verified backup, and restore with both processes stopped. This release step
changes no schema and provides no automatic migration rollback.

## 10. Troubleshooting

| Symptom | Check privately |
| --- | --- |
| Bot exits at startup | Token presence/format, APP_ENV, numeric allowlist, public HTTPS BASE_URL, absolute DB path; sanitized error category |
| Telegram timeout | Proxy routing/bypasses, egress/DNS/TLS; both clients already have 30-second connect/read settings |
| Polling conflict | Another instance/process uses the same token; stop the duplicate |
| Missing tables or empty link list | Wrong database path or initialization omitted; do not initialize over an unreviewed legacy database |
| Read-only/locked database | Directory and journal permissions, shared path, disk space, competing processes, SQLite contention |
| 502 at proxy | Web service/bind, firewall, selected Gunicorn installation, Nginx configuration |
| 410 link | Stored inactive/expired/invalid expiration; duplicates retain the original expiration |
| CSV empty/truncated | UTC [start,end) semantics, creation-vs-event dates, 5,000-row/8 MiB limits |
| CSV upload fails | Named InputFile path is implemented; check network/timeouts; retry with smaller range without logging report bytes |
| Admin denied | Authenticated numeric sender ID and private chat, not username or command arguments |
| Local links fail on a phone | Loopback refers to the phone; configure a genuinely public HTTPS origin for production |

## 11. Future organization deployment

Deploy a separate instance with a separate token/Telegram username, administrator
allowlist, service environment, database, hostname, backup/retention policy, and
operator ownership. Do not repurpose the portfolio database or export real user
records into Git. Branding changes, bot-management settings, organizational rights,
and privacy policies require a separate reviewed change; none are implemented here.
Confirm source/third-party licensing and publication permission before reuse.

## 12. Operational limits and references

Public links can be enumerated and are not authorization. Destinations are not
reputation-checked or fetched by the service. Bot classification is heuristic;
counts include previews/HEAD. Legacy malformed/non-UTC expiration rows can disagree
with duplicate text comparisons; audit them rather than silently rewriting data.
Eligibility checks precede click writes, so an in-flight request can race expiry.
Date filtering uses SQLite millisecond precision. No performance/load certification,
abuse quotas, event-retention automation, or multi-host database support is claimed.

Operator references: [Gunicorn deployment](https://gunicorn.org/deploy/),
[Gunicorn settings](https://gunicorn.org/reference/settings/),
[Nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
[systemd services](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[Python SQLite backup API](https://docs.python.org/3.14/library/sqlite3.html#sqlite3.Connection.backup),
and [Certbot instructions](https://certbot.eff.org/instructions).
These references do not replace validation of the selected OS/package versions.
