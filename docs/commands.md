# JAYSPRAY command guide

JAYSPRAY is Linux-only. Global options go before the command:

```bash
jayspray --config /etc/jayspray/config.toml <command> [options]
```

If `--config` is omitted, JAYSPRAY uses `/etc/jayspray/config.toml` or the absolute path in
`JAYSPRAY_CONFIG`. Samsung requires a model and sales CSC to query firmware history, so add
at least one enabled `[[targets]]` entry before discovery. CSC identifies a request route;
canonical releases are deduplicated by model + PDA/AP.

## Safe first run

```bash
jayspray --config /etc/jayspray/config.toml sync --dry-run
jayspray --config /etc/jayspray/config.toml discover --limit 10
jayspray --config /etc/jayspray/config.toml search SM-S928U1
jayspray --config /etc/jayspray/config.toml probe --first 10
```

These commands do not download a firmware payload. A real multi-gigabyte download begins
only with `download` or with `sync` when `download.automatic = true`.

## Effects at a glance

| Command | Samsung metadata request | Catalog write | Firmware download | Extraction |
| --- | ---: | ---: | ---: | ---: |
| `discover` | Yes | Yes | No | No |
| `discover --dry-run` | Yes | No | No | No |
| `sync --dry-run` | Yes | No | No | No |
| `sync` | Yes | Yes | Only if configured | Only if configured |
| `inspect` | Yes | No | No | No |
| `probe` | Yes | May update resolution | No | No |
| `backfill` | Yes | Yes | No | No |
| `download` | As required by backend | Yes | Yes | No |
| `extract` | No | Yes | No | Yes |
| `status`, `search`, `show` | No | No after initialization | No | No |

## Discovery and synchronization

`discover` queries bounded official Samsung history for enabled targets, normalizes the
results, merges identical model + PDA releases across CSC routes, and updates SQLite.

```bash
jayspray discover
jayspray discover --limit 10
jayspray discover --dry-run
```

`--limit` bounds normalized candidates across the run. Dry run uses an in-memory snapshot:
it contacts Samsung but does not persist database or firmware-file changes.

`sync` performs discovery and then applies automatic download/extraction policy:

```bash
jayspray sync --dry-run
jayspray sync
```

Keep `download.automatic = false` for metadata-only daily operation. Dry run reports new,
matched, queued, downloadable, and skipped work without changing persistent state.

## Compare CSC routes

`inspect` compares official history directly without changing the database:

```bash
jayspray inspect SM-S928U1 --csc XAA --csc VZW --history-limit 10
```

`MERGE` means multiple routes reported the same PDA and would become one canonical release.
`UNIQUE` means only one supplied route reported that PDA. This does not claim that regional
CSC/CP components are byte-identical; their exact version tuples remain separate provenance.

## Probe downloadability

`probe` checks whether cataloged exact versions still appear in current Samsung history. It
does not retrieve the multi-gigabyte payload:

```bash
jayspray probe --first 10
```

An `[OK]` result means the release is resolvable through the official Samsung path. `[FAIL]`
includes a sanitized reason and produces a nonzero result when any selected item fails.

## Download

Download one selected release sequentially:

```bash
jayspray download --first 1
jayspray download --id <firmware-id>
```

Before starting, JAYSPRAY prints the number of packages, model, route CSC, exact version, and
known size. It checks free space, writes `firmware.zip.partial`, validates the resulting ZIP,
computes SHA-256, atomically renames the file, and records the artifact. Repeating the command
returns the verified existing artifact instead of downloading it again.

More than one package requires an explicit acknowledgement because firmware is large:

```bash
jayspray download --first 10 --yes
```

The current backend retries ranges within one running process but does not resume an old
partial file after restart. A stale partial is removed before retry.

## Extract and catalog

Extract an already verified decrypted ZIP:

```bash
jayspray extract <firmware-id>
```

JAYSPRAY rejects unsafe archive paths, special files, excessive sizes, too many members, and
suspicious compression ratios. It generates `manifest.json` with every extracted file's
relative path, size, SHA-256, Samsung component classification, and `.tar.md5` indicator.
It does not recursively unpack `.tar.md5` files and never flashes a device.

## Search, show, and status

```bash
jayspray search SM-S928U1
jayspray search --csc XAA
jayspray search --pda S928U1UES4
jayspray show <firmware-id>
jayspray status
```

`search` lists matching canonical releases. `show` emits one release, every observed CSC
route, and cataloged artifacts as JSON. `status` reports state counts, target checkpoints,
the latest run, unresolved failures, and disk capacity.

## Explicit history backfill

Normal discovery is intentionally shallow. Request a bounded deeper official-history ingest
only when needed:

```bash
jayspray backfill --history-limit-per-target 50
jayspray backfill --history-limit-per-target 50 --limit 100
```

This never downloads payloads. Both bounds accept values from 1 through 10,000.

## Exit status

- `0`: requested operation completed successfully.
- `1`: configuration, target, backend, archive, storage, or selected-probe failure.
- `2`: unsupported platform or command-line parsing error.

Diagnostics are written without intentional secrets. Do not post unsanitized service
environments, proxy URLs, local databases, or firmware metadata in public channels.
