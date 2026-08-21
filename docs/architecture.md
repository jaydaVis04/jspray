# Architecture decisions

## Pipeline

The Python orchestration package owns a configured model/CSC watch list, normalization,
SQLite state, scheduling, policy, verification, extraction, and cataloguing. For each
enabled target it asks the Samsung/FUS backend for firmware history, records newly seen
official versions, and queues them according to policy. A single download worker invokes
the Samsung/FUS backend and then validates, catalogues, and extracts the resulting ZIP.

The stages are:

`Samsung history -> canonicalize -> queue -> download -> verify -> decrypt -> extract -> catalog`

The `decrypt` state records that the backend output is a decrypted archive even though
the backend performs download and decryption in one process.

## Canonical identity

Samsung requires a sales CSC for FUS lookup, but the operator's catalog is PDA-oriented.
Canonical identity is therefore:

- Canonical key: `MODEL NUL AP/PDA`

Values are trimmed and uppercased before a SHA-256 key is calculated. Samsung history
normally supplies the AP/CSC/CP slash tuple and may supply a fourth data component. Every
CSC-specific returned string is preserved as an observation, but two observations with the
same model and PDA attach to one release even if their CSC/CP components differ. The first
configured successful route supplies the exact version and CSC used for the one payload
download. This is an intentional operator policy, not a claim that regional binaries are
byte-identical.

SQLite enforces unique model+PDA identities and unique official Samsung history observations.
Downloaded firmware ZIPs are keyed by binary SHA-256, giving a second content-level
deduplication boundary.

## Incremental discovery

Each watch target records its last check and last observed Samsung version. A normal sync
requests Samsung history once per target and inserts only unseen exact versions. A target
failure is isolated so other targets continue. There is no third-party HTML, pagination,
parser, cross-site agreement, or website checkpoint layer.

## Persistence and restart safety

SQLite runs with foreign keys, WAL, a busy timeout, explicit transactions, and migrations.
Canonical releases and source observations are distinct. Runs, failures, source
checkpoints, artifacts, and queued work are durable.

State transitions are validated:

`DISCOVERED -> RESOLVED -> QUEUED -> DOWNLOADING -> DOWNLOADED -> VERIFIED -> DECRYPTED -> EXTRACTED`

Failures can occur from any active state and retry back to a queue. A process lock prevents
overlapping systemd and manual mutation runs. Files use deterministic paths and temporary
names; completion is only committed after verification and atomic rename.

## Security boundaries

- Backend subprocesses use argument arrays, never a shell, and validate model, CSC, and
  version strings.
- Logs redact secret-bearing keys and never store HTML bodies or authentication tokens.
- ZIP extraction rejects absolute paths, traversal, symlinks, device-like entries,
  excessive member counts, oversized output, and suspicious compression ratios.
- No flashing code exists. Website login and CAPTCHA automation are out of scope.
