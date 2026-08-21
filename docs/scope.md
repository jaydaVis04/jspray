# Current project scope

Last changed: 2026-08-21

This file is the authoritative scope checkpoint for future work.

## Active scope

JAYSPRAY is a Linux-only, headless tool with two deliberately separate responsibilities:

- discover new worldwide model/PDA releases from public SamFrew and SamMobile latest feeds
- resolve and retrieve those releases from Samsung using the FUS workflow investigated
  through Bifrost

The official-payload stages cover Samsung history and binary metadata, official firmware
download, `.enc2`/`.enc4` decryption to ZIP, safe extraction, manifests, SQLite state, CLI,
and systemd automation.

Bifrost cannot enumerate every model/CSC by itself; Samsung requires a known pair. The
indexes provide model, PDA, and one or more observed CSC routes. CSC is retrieval/provenance
metadata here, not canonical identity.

The canonical release identity is exactly `normalized model + PDA/AP`. If XAA, EUX, and INS
list the same PDA, they are observations of one canonical release and only one payload is
queued. The first observed route that resolves the PDA through Samsung becomes the payload
route. SHA-256 supplies a second, binary-level duplicate boundary.

## Index boundaries

SamFrew and SamMobile are metadata-only discovery sources. JAYSPRAY uses ordinary HTTPS and
static parsing, keeps requests bounded, stores provenance, and never uses their download
pages as the preferred payload path. SamFW currently denies ordinary HTTP access and remains
disabled; no CAPTCHA, authentication, paywall, or anti-bot bypass is permitted.

## Bifrost terminology correction

Bifrost is not a global scraper or worldwide firmware feed. `VersionFetch` resolves history
for a supplied model and CSC through Samsung SmartHistory with a FOTA fallback. `Request`
retrieves Samsung binary metadata; the FUS client downloads the encrypted payload; the
downloader checks available integrity metadata; and `Decrypter` produces the ZIP. Archive
extraction is a separate JAYSPRAY step.
