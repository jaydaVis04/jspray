# Architecture decisions

## Pipeline

The Python orchestration package owns fixed-origin discovery adapters, normalization, SQLite
state, scheduling, policy, verification, extraction, and cataloguing. It fetches the newest
index pages concurrently, merges repeated model/PDA observations, then uses an observed CSC
to resolve the exact version through Samsung. A single download worker invokes the FUS
backend and validates, catalogues, and extracts the resulting ZIP.

`indexes -> canonicalize -> Samsung resolve -> queue -> download -> verify -> decrypt -> extract -> catalog`

The `DECRYPTED` state records that the backend output is a decrypted archive even though the
backend performs download and decryption in one process.

## Canonical identity

Samsung requires a sales CSC for FUS lookup, but the catalog is PDA-oriented. Canonical
identity is the SHA-256 of `normalized MODEL NUL normalized AP/PDA`. Country and CSC never
create a second release with the same model/PDA.

Every index record is preserved as a source observation, including source URL and CSC. A
probe tries unique observed CSC routes until Samsung returns a complete exact AP/CSC/CP
version. Downloaded ZIPs are keyed by SHA-256 for content-level deduplication.

## Incremental discovery

A normal sync requests only the configured number of newest pages—one per source by default—
and inserts only unseen source record keys. Adapters run concurrently with bounded retries,
timeouts, and response sizes. One source or parser failure is isolated so the others
continue. Fixture tests make “parser broken” distinct from “zero new firmware.” Deeper
SamFrew pagination must be explicitly configured.

## Persistence and restart safety

SQLite runs with foreign keys, WAL, a busy timeout, explicit transactions, and packaged
migrations. Canonical releases and source observations are distinct. Runs, failures,
artifacts, and queued work are durable.

States progress through:

`DISCOVERED -> RESOLVED -> QUEUED -> DOWNLOADING -> DOWNLOADED -> VERIFIED -> DECRYPTED -> EXTRACTED`

A process lock prevents overlapping mutation runs. Firmware is written to `.partial`,
verified, then atomically renamed. Existing verified artifacts are reconciled after an
interrupted database commit.

## Security boundaries

- Discovery is HTTPS-only, restricted to hard-coded source hosts, response-size limited,
  unauthenticated, and never logs complete HTML.
- Backend subprocesses use argument arrays, never a shell, and validate model, CSC, and
  version strings. The executable must be an absolute, non-writable regular file and may be
  pinned by SHA-256.
- Logs and persisted failures redact secret-bearing keys and common credential forms.
- ZIP extraction rejects absolute paths, traversal, symlinks, special files, excessive
  member counts, oversized output, and suspicious compression ratios.
- Website login, CAPTCHA automation, access-control bypass, and device flashing are absent.
