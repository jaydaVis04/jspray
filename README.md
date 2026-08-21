# JAYSPRAY

JAYSPRAY is a Linux-only command-line tool that finds recently listed Samsung firmware,
resolves the newest official package for each model and region, downloads it from Samsung,
verifies and decrypts it, extracts it safely, and catalogs the result.

It never flashes a phone and never uses firmware-index websites as payload mirrors.

## How it works

```text
SamFrew + SamMobile (+ SamFW when ordinarily accessible)
                    |
             model + region only
                    |
          last 21 days, one region per model
                    |
       optional metadata.json model exclusion
                    |
       Samsung latest-version lookup and FUS download
                    |
          verify -> decrypt ZIP -> extract -> manifest
```

The public indexes are discovery signals. JAYSPRAY ignores their PDA values when deciding
what to process. It keeps every model/region observation for provenance, selects only one
region for each model during a run, and asks Samsung for the current package for that pair.
Samsung's returned exact version is retained only as internal download/catalog metadata.

Discovery is limited to dated entries from the configured lookback window, which defaults
to 21 days. Undated and older records are skipped. SamFW currently rejects ordinary HTTP
access and is disabled by default; JAYSPRAY does not bypass anti-bot controls.

## Relationship to Bifrost

[Bifrost](https://github.com/zacharee/Bifrost) proved the official workflow JAYSPRAY uses:
given a model and Samsung sales region/CSC, resolve the latest version through Samsung,
retrieve binary metadata from FUS, download the encrypted `.enc2` or `.enc4` payload, and
decrypt it into a firmware ZIP.

Bifrost is a Compose GUI and has no supported headless CLI/library boundary. JAYSPRAY does
not automate that GUI or copy its code. Its replaceable backend invokes `samloader-rs` 2.x,
a headless Samsung FUS client, then JAYSPRAY separately validates and extracts the resulting
ZIP. See [Bifrost research](docs/bifrost-research.md) and
[third-party notices](THIRD_PARTY_NOTICES.md).

## Use an existing metadata file

A user can point JAYSPRAY at an existing, even million-line, `metadata.json` file:

```toml
[metadata]
path = "/srv/catalog/metadata.json"
append_completed = false
```

Before resolution or download, JAYSPRAY scans that file for normalized Samsung model tokens
such as `SM-S928U1`. If a discovered model appears anywhere in the file, every region for
that model is skipped. The scan builds a SQLite index once; later runs read only newly
appended bytes unless the file was replaced, truncated, or rewritten. Lookups are indexed
SQLite queries, not a new full-file `grep` for every model.

The input may be JSON, JSON Lines, or other line-oriented text for model checking. The path
must be absolute, must name a regular file, and symlinks are refused. A configured missing
or unreadable file stops the run so JAYSPRAY cannot accidentally download known models.

Appending is deliberately opt-in. The current writer supports one JSON object per line
(JSONL) and appends these keys after a verified download: `model`, `region`, `full_version`,
`firmware_release_id`, `artifact`, `sha256`, `completed_at`, and `source`. It locks and
flushes the file before refreshing the cache. Top-level JSON arrays are scan-only and are
never rewritten. Keep `append_completed = false` until the catalog's exact record format
has been confirmed; a format-specific writer can then preserve that schema exactly.

The local artifact catalog is also checked by model. Therefore a verified download remains
protected from a second regional download even while external-file appending is disabled.

Do not commit a private metadata catalog. Its contents remain in the configured external
file and the local SQLite cache; JAYSPRAY does not print the file contents.

## Install on Linux

Requirements are Linux, Python 3.11+, `git`, and a trusted `samloader-rs` 2.x executable.
From a fresh clone:

```bash
python3 -m venv .venv
.venv/bin/pip install .
cp config.example.toml config.toml
```

Edit `config.toml` to use writable absolute paths and the absolute path to `samloader`.
For a local non-root test, paths can live under `/var/tmp/jayspray`. If a private CA is used,
install its certificate in the operating system trust store; do not disable TLS validation.

## Safe first run

```bash
.venv/bin/jayspray --config "$PWD/config.toml" sync --dry-run
```

This reads the newest index pages and explains what would resolve, download, or skip. It
does not persist discovery changes and does not download firmware.

Useful commands:

```bash
# Persist recent model/region discovery only
.venv/bin/jayspray --config "$PWD/config.toml" discover --limit 10

# Ask Samsung about the first ten unique models; no payload download
.venv/bin/jayspray --config "$PWD/config.toml" probe --first 10

# Direct Bifrost-style model + region workflow, one real large download
.venv/bin/jayspray --config "$PWD/config.toml" download --model SM-S928U1 --region XAA

# Or download the first discovered model
.venv/bin/jayspray --config "$PWD/config.toml" download --first 1

# Extract a verified download and generate its manifest
.venv/bin/jayspray --config "$PWD/config.toml" extract <firmware-release-id>

# Search targets and inspect status
.venv/bin/jayspray --config "$PWD/config.toml" search SM-S928U1
.venv/bin/jayspray --config "$PWD/config.toml" search --region XAA
.venv/bin/jayspray --config "$PWD/config.toml" status
```

Multiple downloads require both `--first N` and `--yes`. Downloads run sequentially, check
free space, write `.partial` files, validate ZIP members and CRCs, compute SHA-256, and only
then atomically mark the artifact complete. See the [command guide](docs/commands.md).

## Configuration and storage

Copy [config.example.toml](config.example.toml) and set:

- enabled indexes, request timeouts, retry policy, delay, and 21-day lookback;
- SQLite, download, extraction, cache, and state paths;
- optional external metadata path and append policy;
- absolute `samloader` path and optional executable SHA-256 pin;
- free-space threshold and automatic download/extraction policy.

Default storage is:

```text
/var/lib/jayspray/
├── database/firmware.db
├── downloads/<model>/<resolved-version>/<release-id>/firmware.zip
├── extracted/<model>/<resolved-version>/<release-id>/manifest.json
├── cache/
└── state/jayspray.lock
```

SQLite migrations preserve target observations, resolved releases, artifacts, runs,
failures, and the external metadata model index. Extracted manifests list every filename,
relative path, size, SHA-256, Samsung component classification, and nested `.tar.md5` file.

## Daily systemd operation

Install the files from [packaging/systemd](packaging/systemd), adjust paths if needed, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jayspray.timer
systemctl list-timers jayspray.timer
journalctl -u jayspray.service
```

The persistent randomized timer catches missed runs. The service runs as an unprivileged
user. With `download.automatic = false`, daily sync performs metadata discovery and Samsung
resolution only. Enable automatic download or extraction only after verifying storage and
the external metadata policy.

The supplied service can write `/var/lib/jayspray`. If an append-enabled metadata file is
elsewhere, add that exact directory to the service's `ReadWritePaths`; files under a home
directory remain inaccessible because `ProtectHome=true`.

## Safety and privacy

No website account is required. JAYSPRAY stores no passwords, browser cookies, or Samsung
tokens. Logs redact common credential forms and never include complete HTML or metadata-file
contents. The backend uses argument arrays rather than a shell and receives a restricted
environment. Archive extraction rejects traversal, links, special files, and suspicious
sizes or compression ratios.

JAYSPRAY is owner-maintained and does not accept external contributions. The public may
clone and use it under the repository license.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest
./scripts/test-linux.sh
```

Normal tests mock Samsung and never download a firmware package. Parser changes belong in
one isolated adapter with a fixture test so a broken source cannot silently look empty.
