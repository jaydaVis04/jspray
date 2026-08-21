# JAYSPRAY command guide

JAYSPRAY runs only on Linux. Put global options before the command:

```bash
jayspray --config /etc/jayspray/config.toml <command> [options]
```

Without `--config`, it reads `/etc/jayspray/config.toml` or `JAYSPRAY_CONFIG`.

## Command effects

| Command | Public indexes | Samsung lookup | Database write | Payload | Extract |
| --- | ---: | ---: | ---: | ---: | ---: |
| `discover` | Yes | No | Yes | No | No |
| `discover --dry-run` | Yes | No | No | No | No |
| `sync --dry-run` | Yes | No | No | No | No |
| `sync` | Yes | Yes | Yes | Only if configured | Only if configured |
| `probe` | No | Yes | Yes | No | No |
| `download` | No | Yes when needed | Yes | Yes | No |
| `extract` | No | No | Yes | No | Yes |
| `status`, `search`, `show` | No | No | Initialization only | No | No |

## Discovery

```bash
jayspray discover
jayspray discover --limit 10
jayspray discover --dry-run
```

Adapters concurrently read a bounded number of newest pages. Only entries dated within
`discovery.lookback_days` (21 by default) are eligible. Results are normalized to model and
region/CSC targets. PDA values from index sites do not control selection or deduplication.

`--limit` bounds unique model/region candidates. Cross-source provenance is retained. One
source failure is reported without preventing successful adapters from being stored.

## Dry-run synchronization

```bash
jayspray sync --dry-run
```

The command uses an in-memory database snapshot. It shows new and matched targets, records
excluded for age, missing dates, or the external metadata file, duplicate regions skipped
because the model was already selected, and whether automatic download would occur. It does
not persist rows or write firmware files.

## Samsung probe

```bash
jayspray probe --first 10
```

The first ten unique models not excluded by external metadata are resolved using one
observed region each. `[OK]` means Samsung returned a complete latest version for that model
and region. `probe` stores resolution metadata but does not download the package.

## Download

Direct model and region, matching Bifrost's required inputs:

```bash
jayspray download --model SM-S928U1 --region XAA
```

Or choose from discovered targets:

```bash
jayspray download --first 1
jayspray download --id <firmware-release-id>
jayspray download --first 10 --yes
```

Before resolution and again before download, JAYSPRAY checks the model against the cached
external metadata index. It also allows only one region per model in a command. If the model
already exists, the command prints the skip reason and does not contact the payload service.

More than one large payload requires `--yes`. Downloads are sequential. JAYSPRAY checks free
space, writes `firmware.zip.partial`, accepts only successful backend completion, checks ZIP
structure and every member CRC, computes SHA-256, and atomically renames the file. Repeating
an already cataloged release returns the verified artifact rather than downloading it again.

When `[metadata] append_completed = true`, a verified result is appended and indexed before
the next model is considered. This is supported only for the documented JSONL record format;
keep it false for a different or not-yet-confirmed schema.

## Extraction

```bash
jayspray extract <firmware-release-id>
```

The command extracts an already verified decrypted ZIP. It rejects absolute/traversal
paths, backslash paths, links, devices, FIFOs, excessive sizes, too many members, and
suspicious compression ratios. `manifest.json` catalogs all output and identifies common
AP, BL, CP, CSC, HOME_CSC, USERDATA, and nested `.tar.md5` members. It never flashes a phone.

## Search, show, and status

```bash
jayspray search SM-S928U1
jayspray search --region XAA
jayspray show <target-id>
jayspray status
```

`search` lists model/region targets and source agreement. `show` emits a target, its source
provenance, latest resolved Samsung release, and artifacts as JSON. `status` reports target,
run, artifact, source, failure, and disk information.

## External metadata file

```toml
[metadata]
path = "/srv/catalog/metadata.json"
append_completed = false
```

The path must be absolute and point to a non-symlink regular file. The first run scans for
`SM-...` model tokens and caches them in SQLite. A growing file is indexed incrementally;
replacement, truncation, or same-size modification causes a safe rebuild. A missing file is
an error rather than a fail-open download.

JSONL appending writes `model`, `region`, `full_version`, `firmware_release_id`, `artifact`,
`sha256`, `completed_at`, and `source`. Top-level JSON arrays and custom JSON structures are
read-only until a matching writer is implemented for their exact schema.

## Exit status

- `0`: operation completed, including a deliberate metadata/model skip.
- `1`: source, configuration, backend, storage, selected resolution, or archive failure.
- `2`: unsupported platform or invalid command-line syntax.
