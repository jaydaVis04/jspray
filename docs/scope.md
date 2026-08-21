# Current project scope

Last changed: 2026-08-21

This file is the authoritative scope checkpoint for future work.

## Active scope

Build a Linux-only, headless tool that uses the same official Samsung firmware services
investigated through Bifrost:

- Samsung FUS / SmartHistory for version history and binary metadata
- Samsung FOTA `version.xml` only as the upstream-style history fallback
- Samsung's firmware binary service for payload download
- `.enc2` / `.enc4` decryption to a verified ZIP
- safe ZIP extraction, component inventory, manifests, SQLite state, CLI, and systemd

Discovery is driven by an explicit configured watch list of Samsung models and one or more
probe CSCs. Bifrost cannot enumerate every model/CSC by itself; Samsung requires a CSC to
perform the request. CSC is transport/provenance metadata here, not canonical identity.

The canonical release identity is `normalized model + PDA/AP`.
If XAA, EUX, and INS probes return the same PDA, they are recorded as three Samsung route
observations of one canonical release and only one payload is queued. Their complete
AP/CSC/CP tuples remain visible because same-PDA regional packages are not guaranteed to
be byte-identical. The first configured successful CSC is the deterministic download route.

## Explicitly cancelled

Do not implement, invoke, test, or schedule any integration with:

- SamFrew
- SamFW
- SamMobile
- their listing pages, detail pages, accounts, or download services

The earlier multi-index discovery requirement is superseded. The project must not retain
website adapter code, website parser dependencies, website credentials, or claims of
cross-site agreement.

## Bifrost terminology correction

Bifrost is not a scraper for the three firmware database websites. Its important behavior
is direct resolution and download through Samsung infrastructure. `VersionFetch` queries
Samsung SmartHistory and can fall back to Samsung FOTA XML; `Request` retrieves Samsung
binary metadata; the FUS client downloads the encrypted payload; the downloader checks
available upstream integrity metadata; and `Decrypter` produces the ZIP. Archive extraction
is still a separate step.
