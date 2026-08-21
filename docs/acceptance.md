# Acceptance record

Date: 2026-08-21

## Automated Linux verification

The repository was installed into a clean `python:3.11-slim` Linux container. The container
completed:

- 34 unit/database/backend/extraction/orchestration/CLI/configuration tests
- Ruff lint
- strict MyPy checking of the package

The container uses a digest-pinned base image and runs the checks as unprivileged UID 10001.

Normal tests use a mock Samsung backend and tiny generated ZIPs. They do not download
firmware. Covered behaviors include:

- two CSC observations with one model+PDA merge into one release
- different PDAs remain distinct
- the first configured CSC is retained as the deterministic download route
- repeated observations and repeated downloads are idempotent
- SQLite uniqueness and state persistence across restart
- a completed atomic ZIP left before a database commit is reconciled, not redownloaded
- dry run leaves the persistent catalog unchanged
- traversal ZIPs are rejected and component manifests are generated
- backend invocation is shell-free, inherits only allowlisted environment values, and
  redacts sensitive diagnostic data
- dry run explains same-PDA duplicates, existing observations, queued work, and disabled
  automatic downloads without persisting changes

## Live official Samsung metadata verification

The official `samloader-rs` 2.0.0 Linux aarch64 release asset was downloaded to temporary
storage. Its SHA-256 matched the digest published in GitHub release metadata:

`9703e49e944d27dc5ac973492bf706035fe5aadc1b10d3a3a68f33f50d91b977`

No payload was downloaded. Metadata-only Samsung history requests returned the same latest
PDA for two CSC routes:

```text
XAA: S928U1UES6DZG1/S928U1OYM6DZG1/S928U1UES6DZG1/S928U1UES6DZG1
VZW: S928U1UES6DZG1/S928U1OYM6DZG1/S928U1UES6DZG1/S928U1UES6DZG1
```

`jayspray inspect` reported one `MERGE` group with two routes. A full metadata discovery then
reported one new release and one merged observation. Repeating the same discovery reported
zero new releases and two matched observations. The catalog contained one canonical PDA
with two Samsung route observations.

## Deliberately not performed

A real Samsung firmware payload is multiple gigabytes. Download, post-download ZIP
verification, extraction, and manifest behavior are implemented and tested with a mock
binary service, but a live payload download was not started without explicit operator
approval and a designated storage budget. No device was flashed.
