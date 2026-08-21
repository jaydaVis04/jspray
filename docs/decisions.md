# Decision log

This is a concise durable memory for implementation decisions and verification results.
It is updated at meaningful milestones, not for every edit.

## 2026-08-21 — Bifrost investigation complete

- Inspected current local Bifrost source at `15936f92` through version history, binary
  metadata, Samsung endpoints, integrity checks, download, and `.enc2`/`.enc4` decryption.
- Confirmed Bifrost has no headless CLI and does not extract the decrypted ZIP.
- Selected maintained `samloader-rs` 2.x as the replaceable Linux Samsung/FUS CLI backend.
- Chose Python for orchestration, SQLite, secure extraction, and systemd
  integration.
- Defined strong/weak canonical identities and conflict-safe matching.
- Confirmed Docker is available for Linux verification; Java is not a viable local build
  dependency for a Kotlin wrapper in this environment.

## 2026-08-21 — Scope changed to Samsung-only

- All SamFrew, SamFW, and SamMobile integration was cancelled.
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

## 2026-08-21 — Public release hardening and JAYSPRAY name

- Renamed the public CLI, Python package, Linux paths, systemd units, and test image to
  JAYSPRAY; the command is `jayspray`.
- Added a dedicated command guide, security policy, documentation index, read-only GitHub
  Actions permissions, and pinned build inputs.
- Made TOML types strict so values such as `automatic = "false"` cannot accidentally enable
  downloads. Added general diagnostic redaction, a minimal downloader environment, optional
  downloader SHA-256 pinning, symlink-resistant managed paths, and non-root container tests.
- Removed dynamic SQL composition from catalog search and added explicit result limits.
- The public tree and complete Git history passed Gitleaks; Semgrep reported zero findings
  across the source/test/packaging tree; the resolved Python environment reported no known
  vulnerabilities after its installer was updated.

## 2026-08-21 — Live end-to-end payload acceptance

- Selected the bounded `SM-J105F / XSG` target after metadata-only probes.
- Downloaded one 1.0 GiB official Samsung payload with the checksum-pinned samloader-rs 2.0.0
  Linux backend, then verified and cataloged the decrypted ZIP.
- Extracted and hashed BL, AP, CP, and CSC `.tar.md5` components under the configured archive
  safety limits and reached the persisted `EXTRACTED` state.
- Repeated discovery matched the existing observation, and repeated download-by-ID returned
  the unchanged verified ZIP without a second transfer or `.partial` file.
- No nested component archive was unpacked and no firmware was flashed.

## 2026-08-21 — Owner-maintained upstream policy

- The public upstream repository is maintained only by its owner and does not accept external
  contributions, feature requests, support requests, or collaborator access requests.
- Removed the contribution guide and Dependabot pull-request configuration. Dependency updates
  are handled during owner maintenance without automated public pull requests.
- Public cloning and use remain available under the MIT license; that license does not grant
  write or merge access to the upstream repository.
- GitHub issues, discussions, projects, and wiki are disabled, and pull-request creation is
  restricted to the sole collaborator. Private vulnerability reporting remains the only
  supported reporting channel.
