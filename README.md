# samsung-fw-sync

`fwtool` is a Linux-only, headless Samsung stock-firmware catalog and retrieval service.
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
is copied. See [the research record](docs/phase-0-research.md),
[current scope](docs/scope.md), and [third-party notices](THIRD_PARTY_NOTICES.md).

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
/var/lib/samsung-fw-sync/
├── database/firmware.db
├── downloads/<model>/<pda>/<release-id>/firmware.zip
├── extracted/<model>/<pda>/<release-id>/manifest.json
├── cache/
└── state/fwtool.lock
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
binary and pin its absolute path in the configuration. Then install this project:

```bash
python3 -m venv /opt/samsung-fw-sync/venv
/opt/samsung-fw-sync/venv/bin/pip install /path/to/samsung-fw-sync
sudo install -d -m 0750 /etc/samsung-fw-sync /var/lib/samsung-fw-sync
sudo install -m 0640 config.example.toml /etc/samsung-fw-sync/config.toml
```

Create a dedicated `samsung-fw-sync` user and make `/var/lib/samsung-fw-sync` writable by
that user. The package intentionally refuses to run its CLI outside Linux.

## Configuration

Edit `/etc/samsung-fw-sync/config.toml`. Repeat `[[targets]]` for each model/CSC route:

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
```

Target order is meaningful: the first route that observes a new PDA becomes its download
route. No website credentials or secrets are required. `FWTOOL_CONFIG` may point to another
absolute config path; do not place secrets in it unless a future backend explicitly needs
them.

## CLI

Discover bounded Samsung history without downloading:

```bash
fwtool discover
fwtool discover --limit 10
```

Preview all database and file effects:

```bash
fwtool sync --dry-run
```

Compare PDA names across CSC routes without changing the database:

```bash
fwtool inspect SM-S928U1 --csc XAA --csc EUX --history-limit 10
```

Re-probe the first ten cataloged releases against current official Samsung history:

```bash
fwtool probe --first 10
```

Download exactly one unresolved release. The command prints its model, route CSC, exact
version, and known size before starting:

```bash
fwtool download --first 1
fwtool download --id <firmware-id>
```

More than one large package requires explicit confirmation:

```bash
fwtool download --first 10 --yes
```

Extract a verified ZIP and create `manifest.json` with every file's path, size, SHA-256,
component classification, and nested `.tar.md5` flag:

```bash
fwtool extract <firmware-id>
```

Search and inspect status:

```bash
fwtool search SM-S928U1
fwtool search --pda S928U1UES4
fwtool search --csc XAA
fwtool status
fwtool show <firmware-id>
```

An explicit deeper official-history ingest is bounded per route:

```bash
fwtool backfill --history-limit-per-target 50
```

## Verification and extraction safety

The backend decrypts while downloading. On success `fwtool` checks the ZIP structure,
validates every member CRC, enforces archive size/count/ratio limits, computes SHA-256, and
then performs an atomic rename. Extraction rejects absolute paths, `..`, backslash paths,
symlinks, devices, FIFOs, and oversized or suspicious entries. Nested `.tar.md5` archives
are cataloged but not recursively unpacked or flashed.

The selected CLI backend does not expose Bifrost's encrypted Samsung CRC32/Content-MD5
results. This is a documented backend capability gap, not silently reported as verified.

## Daily systemd operation

Install [fwtool.service](packaging/systemd/fwtool.service) and
[fwtool.timer](packaging/systemd/fwtool.timer) under `/etc/systemd/system/`, adjust the
executable path if needed, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fwtool.timer
systemctl list-timers fwtool.timer
journalctl -u fwtool.service
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
