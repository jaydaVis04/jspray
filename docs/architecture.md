# Architecture decisions

## Pipeline

```text
bounded concurrent indexes
        -> recent model/region targets
        -> external metadata model exclusion
        -> one region per model
        -> Samsung latest-version resolution
        -> sequential FUS download/decryption
        -> ZIP/CRC/SHA-256 verification
        -> guarded extraction and manifest
```

Python owns adapters, policy, SQLite, scheduling, subprocess isolation, verification,
extraction, and cataloging. A replaceable backend invokes the allowlisted headless Samsung
client. The Bifrost GUI is neither imported nor automated.

## Identities

- Discovery target: normalized `model + region/CSC`.
- Model exclusion: normalized `model` only, across all regions.
- Official release: normalized `model + region + Samsung-returned AP version`.
- Binary duplicate: SHA-256 of the verified decrypted ZIP.

This avoids relying on third-party PDA labels while preserving enough exact Samsung metadata
to reproduce a download. Source observations remain separate and retain provenance.

## Incremental work

Daily discovery fetches only configured newest pages and filters to a 21-day window. Adapters
run concurrently with bounded timeouts, retries, delays, and response sizes. Parser failure
is explicit and isolated.

The external metadata cache stores unique models and file fingerprint/offset in SQLite. The
first scan is linear in file size. Appends scan only new bytes and membership checks use an
indexed query. Replacement, truncation, or rewrite rebuilds the cache to avoid stale models.

## Restart and security

SQLite uses migrations, foreign keys, WAL, transactions, and uniqueness constraints. A
process lock prevents overlapping mutating runs. Firmware is written to `.partial`, verified,
then atomically renamed. States progress through discovery, resolution, queueing, download,
verification, decryption, and extraction.

All source origins are fixed HTTPS hosts. The backend runs without a shell, validates model,
CSC, and version strings, uses a restricted environment, and may be SHA-256 pinned. External
metadata paths must be absolute regular files and symlinks are refused. Extraction rejects
traversal, links, special files, and resource-exhaustion archives. Logs redact credentials
and do not dump HTML or metadata-file contents.
