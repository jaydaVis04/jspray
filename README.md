# JAYSPRAY

`jayspray` is a Linux-only, headless Samsung stock-firmware catalog and retrieval service.
It queries Samsung firmware history for configured model/CSC routes, deduplicates releases
by model + PDA, downloads one official payload through Samsung FUS, validates and hashes
the decrypted ZIP, extracts it safely, and maintains restart-safe SQLite state.

It never flashes a device. It has no SamFrew, SamFW, or SamMobile integration.

## Why Bifrost matters

The implementation was designed after tracing current Bifrost source. Bifrost establishes
the required flow: model + CSC history resolution through Samsung SmartHistory/FOTA,
binary metadata through FUS, official payload download, integrity checking, and `.enc2` /
`.enc4` decryption to ZIP. Bifrost is a Compose GUI without a supported headless entry
point and its download model is UI-coupled.

The replaceable backend currently invokes the maintained `samloader-rs` 2.x CLI, which
uses Samsung FUS, supports exact-version downloads, and decrypts to ZIP. No upstream source
is copied. See [the Bifrost research record](docs/bifrost-research.md),
[current scope](docs/scope.md), [security policy](SECURITY.md), and
[third-party notices](THIRD_PARTY_NOTICES.md).

JAYSPRAY does not import or automate the Bifrost GUI. Bifrost is the investigated reference
that confirms how Samsung resolution, integrity metadata, encrypted download, and decryption
fit together; JAYSPRAY supplies a separate headless Linux workflow around a maintained FUS
CLI backend.

| Responsibility | Bifrost | JAYSPRAY |
| --- | --- | --- |
| User interface | Compose desktop GUI | Linux CLI and systemd service |
| Version lookup | Samsung SmartHistory/FOTA | Samsung history through `samloader-rs` |
| Payload source | Samsung FUS | Samsung FUS |
| Decryption | `.enc2`/`.enc4` to ZIP | Backend produces the decrypted ZIP |
| ZIP extraction/catalog | Not provided | Guarded extraction, hashes, and SQLite catalog |
| Source reuse | Upstream project | No Bifrost source is copied |

## PDA-oriented deduplication

Samsung requests require a sales CSC, even though country is not important to this catalog.
Configure one or more CSC probe routes per model. Exact Samsung history strings are stored
for every route, while canonical identity is:

`normalized Samsung model + AP/PDA (the first history-version component)`

If XAA and EUX return the same PDA, SQLite stores one release, two route observations, and
queues one download using the first configured successful CSC. Complete CSC/CP/data tuples
remain auditable because regional packages with the same PDA are not asserted to be
byte-identical. A unique SHA-256 blob catalog adds content-level deduplication after download.

## Pipeline and storage

`Samsung history -> PDA dedupe -> queue -> FUS download/decrypt -> ZIP verify -> SHA-256 -> extract -> manifest`

Default state:

```text
/var/lib/jayspray/
├── database/firmware.db
├── downloads/<model>/<pda>/<release-id>/firmware.zip
├── extracted/<model>/<pda>/<release-id>/manifest.json
├── cache/
└── state/jayspray.lock
```

SQLite uses migrations, WAL, foreign keys, a busy timeout, database uniqueness constraints,
and explicit firmware states. Firmware is written as `.partial`, verified, then atomically
renamed. The current samloader downloader retries ranges within one process but truncates
on a new process, so cross-process byte resume is not claimed.

## Linux installation

Requirements:

- Linux with Python 3.11+
- `samloader-rs` 2.x installed as an executable
- Enough storage for the ZIP plus extracted contents

Install `samloader` using an upstream Linux release or `cargo install samloader`. Verify the
binary, install it where unprivileged users cannot modify it, and pin its absolute path and
optional SHA-256 in the configuration. Then install this project:

```bash
python3 -m venv /opt/jayspray/venv
/opt/jayspray/venv/bin/pip install /path/to/jayspray
sudo install -d -m 0750 /etc/jayspray /var/lib/jayspray
sudo install -m 0640 config.example.toml /etc/jayspray/config.toml
```

Create a dedicated `jayspray` user and make `/var/lib/jayspray` writable by
that user. The package intentionally refuses to run its CLI outside Linux.

## Configuration

Edit `/etc/jayspray/config.toml`. Repeat `[[targets]]` for each model/CSC route:

```toml
[[targets]]
model = "SM-S928U1"
csc = "XAA"

[[targets]]
model = "SM-S928U1"
csc = "EUX"

[discovery]
history_limit_per_target = 5

[download]
automatic = false
automatic_extract = false
concurrency = 1
connections_per_file = 1
minimum_free_bytes = 16106127360
samloader_executable = "/usr/local/bin/samloader"
# samloader_sha256 = "<verified 64-character digest>"
```

Target order is meaningful: the first route that observes a new PDA becomes its download
route. No website credentials or secrets are required. `JAYSPRAY_CONFIG` may point to another
absolute config path; do not place secrets in it unless a future backend explicitly needs
them.

The downloader subprocess receives only locale, certificate, and proxy-related environment
variables; unrelated parent-process secrets are not inherited. Treat authenticated proxy
URLs as secrets and keep them in the service manager's protected environment, never Git.

## CLI

The safest first run is `jayspray sync --dry-run`, followed by metadata-only discovery and
probing. Payload download never happens from `discover`, `inspect`, or `probe`. See the
[complete command guide](docs/commands.md) for effects, examples, safeguards, and exit codes.

Discover bounded Samsung history without downloading:

```bash
jayspray discover
jayspray discover --limit 10
```

Preview all database and file effects:

```bash
jayspray sync --dry-run
```

Compare PDA names across CSC routes without changing the database:

```bash
jayspray inspect SM-S928U1 --csc XAA --csc EUX --history-limit 10
```

Re-probe the first ten cataloged releases against current official Samsung history:

```bash
jayspray probe --first 10
```

Download exactly one unresolved release. The command prints its model, route CSC, exact
version, and known size before starting:

```bash
jayspray download --first 1
jayspray download --id <firmware-id>
```

More than one large package requires explicit confirmation:

```bash
jayspray download --first 10 --yes
```

Extract a verified ZIP and create `manifest.json` with every file's path, size, SHA-256,
component classification, and nested `.tar.md5` flag:

```bash
jayspray extract <firmware-id>
```

Search and inspect status:

```bash
jayspray search SM-S928U1
jayspray search --pda S928U1UES4
jayspray search --csc XAA
jayspray status
jayspray show <firmware-id>
```

An explicit deeper official-history ingest is bounded per route:

```bash
jayspray backfill --history-limit-per-target 50
```

## Verification and extraction safety

The backend decrypts while downloading. On success `jayspray` checks the ZIP structure,
validates every member CRC, enforces archive size/count/ratio limits, computes SHA-256, and
then performs an atomic rename. Extraction rejects absolute paths, `..`, backslash paths,
symlinks, devices, FIFOs, and oversized or suspicious entries. Nested `.tar.md5` archives
are cataloged but not recursively unpacked or flashed.

The selected CLI backend does not expose Bifrost's encrypted Samsung CRC32/Content-MD5
results. This is a documented backend capability gap, not silently reported as verified.
The current upstream binary transport behavior is also documented in
[the security policy](SECURITY.md); only deploy verified downloader builds on trusted networks.

## Daily systemd operation

Install [jayspray.service](packaging/systemd/jayspray.service) and
[jayspray.timer](packaging/systemd/jayspray.timer) under `/etc/systemd/system/`, adjust the
executable path if needed, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jayspray.timer
systemctl list-timers jayspray.timer
journalctl -u jayspray.service
```

The timer is persistent and randomized. With `automatic=false`, daily sync only updates
metadata. With `automatic=true`, only newly discovered canonical PDAs download sequentially;
`automatic_extract=true` also extracts them.

## Development and tests

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/mypy src
```

Ordinary tests mock Samsung and use tiny ZIPs. A real firmware package is never downloaded
during unit tests. Run Linux verification in Docker with `scripts/test-linux.sh`. The
metadata-only live acceptance procedure and latest evidence are in
[docs/acceptance.md](docs/acceptance.md).

JAYSPRAY is owner-maintained and does not accept external contributions, feature requests,
support requests, or collaborator requests. People may clone and use it under the MIT
license. See the [maintenance policy](MAINTENANCE.md) and use only the private process in
[SECURITY.md](SECURITY.md) for vulnerability reports.

## Troubleshooting

- `samloader executable not found`: install samloader-rs 2.x and configure an absolute path.
- no targets: add at least one enabled `[[targets]]`; Samsung cannot discover without CSCs.
- target failure: confirm the model and CSC are valid together. Other targets still run.
- insufficient space: increase free space or adjust the conservative threshold knowingly.
- same PDA shown under several CSCs: expected; `search` shows one release and multiple routes.
- extraction rejected an archive: keep the ZIP for diagnosis and do not bypass path/size guards.
- interrupted download: rerun it. Current backend restarts that file rather than resuming bytes.

When Samsung/Bifrost behavior changes, update the backend module and fixture/mock contract;
PDA identity, SQLite, extraction, and scheduling remain isolated from protocol details.
