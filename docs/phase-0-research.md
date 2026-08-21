# Phase 0 Bifrost research record

Research date: 2026-08-21

The operator cancelled all third-party firmware database integrations. This record covers
only the behavior that remains relevant: Bifrost and official Samsung infrastructure.
See `docs/scope.md` for the authoritative scope.

## Bifrost source inspected

The local `Bifrost/` checkout was inspected at commit
`15936f926aa4404491a8e2318c1e3e8aa113ca85` (version 2.1.3, 2026-08-09).

- Bifrost is a current Compose Multiplatform GUI application. Linux is supported, but no
  headless CLI entry point exists.
- `VersionFetch.hybridGetLatestVersion(model, region)` asks Samsung SmartHistory first and
  falls back to Samsung FOTA `version.xml`. It requires a model and Samsung sales CSC.
- Bifrost's apparent country input is a chooser backed by `CSCDB`. Country and carrier are
  display/search metadata for a three-character CSC; the actual history request sends
  `BINARY_MODEL_NAME` and `BINARY_LOCAL_CODE`. The tool therefore stores/probes CSC codes,
  not country names.
- `Request.retrieveBinaryFileInfo()` sends the exact version, model, and CSC to Samsung FUS
  binary-inform. It obtains path, encrypted filename, size, CRC32, encryption-generation
  data, and served component metadata.
- Bifrost verifies that the AP, CSC, CP, and optional data component served by Samsung match
  the requested exact version. This proves that CSC-specific tuples can differ even when
  their AP/PDA component is the same.
- `IFusClient.downloadFile()` obtains the payload from Samsung firmware infrastructure.
  Current control requests use HTTPS, while the current binary URL is HTTP; upstream
  encrypted CRC32/Content-MD5 validation is therefore important.
- The GUI downloader checks Samsung CRC32 and Content-MD5 when those values are supplied.
- `Decrypter` supports `.enc2` and `.enc4`, derives or obtains their AES keys, and produces
  a decrypted firmware ZIP. It does not extract the ZIP.
- Directly importing current Bifrost common code would pull in Compose resources, platform
  settings, UI download models, and KMP wiring. There is no stable headless library boundary.
- Bifrost is MIT licensed at repository level. One MD5 helper retains a GPLv2-origin notice.
  No Bifrost code is copied into this project.
- Current diagnostic code prints some authorization material, which a direct production
  headless fork would need to remove.

## What “discovery” means with Bifrost/Samsung

Bifrost does not enumerate all Samsung devices, CSCs, or PDAs. It resolves versions for a
supplied `model + CSC`. The production tool therefore accepts a configured list of models
and probe CSCs. Each CSC query can return multiple exact AP/CSC/CP history strings.

The operator wants PDA-oriented deduplication. Accordingly:

- request key: `model + CSC` (required by Samsung)
- observation key: `model + CSC + complete Samsung history version`
- canonical release key: `model + AP/PDA`
- download route: the first configured successful CSC observation for that PDA

This prevents repeated downloads when several countries expose the same PDA. It preserves
the complete per-CSC observations and does not falsely assert that their regional packages
would have been byte-identical.

## Headless backend decision

Use `topjohnwu/samloader-rs` 2.x as an external, allowlisted Linux CLI backend. It is a
maintained Rust Samsung FUS implementation, explicitly credits Bifrost and related work,
supports exact-version downloads, decrypts in place to a ZIP, and is Apache-2.0/MIT.
The orchestration layer invokes only `check-update` and `download`; it never invokes flash
or device-management commands.

Verified limitations of its current downloader:

- It opens output with `truncate(true)`, preallocates, downloads ranges, and decrypts in
  place. It retries stalled ranges during one process but cannot resume an existing output
  across process restart.
- Its public CLI does not expose Bifrost's encrypted CRC32/Content-MD5 result. The wrapper
  writes a `.partial` file, accepts only exit status zero, validates every decrypted ZIP
  member CRC, computes SHA-256, and atomically renames it. This limitation is documented.
- `check-update --all` is a metadata-only history probe. It does not perform binary-init.

## Extraction conclusion

Decryption and extraction are separate. The backend produces the decrypted ZIP. Python's
`zipfile` then applies traversal, symlink, member-count, size, and compression-ratio guards.
It catalogs AP, BL, CP, CSC, HOME_CSC, USERDATA, and nested `.tar.md5` members. It never
flashes a device.

## Remaining acceptance blocker

Development is occurring on macOS. Docker is available for Linux verification, but a real
multi-gigabyte firmware download must not start without an explicit operator confirmation
and an adequate configured Linux storage target.
