# Work log

This is a concise durable memory for implementation decisions and verification results.
It is updated at meaningful milestones, not for every edit.

## 2026-08-21 — Phase 0 complete

- Inspected the live SamFrew, SamFW, and SamMobile listing/detail behavior.
- Inspected current local Bifrost source at `15936f92` through version history, binary
  metadata, Samsung endpoints, integrity checks, download, and `.enc2`/`.enc4` decryption.
- Confirmed Bifrost has no headless CLI and does not extract the decrypted ZIP.
- Selected maintained `samloader-rs` 2.x as the replaceable Linux Samsung/FUS CLI backend.
- Chose Python for source adapters, orchestration, SQLite, secure extraction, and systemd
  integration.
- Defined strong/weak canonical identities and conflict-safe matching.
- Confirmed Docker is available for Linux verification; Java is not a viable local build
  dependency for a Kotlin wrapper in this environment.

## 2026-08-21 — Scope changed to Samsung-only

- The operator cancelled all SamFrew, SamFW, and SamMobile integration.
- No third-party website adapter work will be committed or retained.
- Clarified that Bifrost queries Samsung FUS/SmartHistory/FOTA for a supplied model + CSC;
  it does not enumerate all releases or scrape the cancelled firmware databases.
- Replaced multi-index discovery with a configured Samsung model/CSC watch list.
- Kept the Bifrost-derived concerns that still apply: Samsung resolution, binary metadata,
  integrity checks, encrypted download, decryption, and separate secure ZIP extraction.

## 2026-08-21 — PDA-oriented deduplication

- Confirmed from Bifrost that Samsung FUS requests still require model + sales CSC.
- CSC is now a probe/download route only, not canonical identity.
- Canonical release key is normalized model + PDA/AP.
- Same-PDA results from multiple CSCs merge into one release and one download; each exact
  CSC-specific Samsung version remains an observation for provenance and diagnostics.
- The configured target order determines the representative download route.

## 2026-08-21 — Linux and live metadata acceptance

- Clean Linux Python 3.11 container passed tests, lint, and strict typing.
- Checksum-verified samloader-rs 2.0.0 successfully queried official Samsung history.
- SM-S928U1 routes XAA and VZW returned the same current PDA and merged into one release.
- Repeating discovery created zero new releases, proving metadata idempotency.
- No firmware payload was downloaded and no device was flashed.
