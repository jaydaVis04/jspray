# Security policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting feature for this repository. Do not open a
public issue containing credentials, authorization headers, proxy URLs, private firmware
metadata, local paths, database contents, or exploit details. Include the affected version,
impact, reproduction steps, and a minimal sanitized example.

Private vulnerability reports do not create a contributor role or permission to modify the
upstream repository. The owner implements accepted fixes. Do not send a patch unless the owner
specifically requests one.

## Supported version

Security fixes are applied to the current `main` branch until versioned releases establish
a longer support policy.

## Trust boundaries

- `jayspray` never flashes devices and does not need device access.
- No website credentials are required. Configuration files must not contain secrets.
- Install `samloader` at an absolute path that the service user cannot modify. Pin its
  SHA-256 with `download.samloader_sha256` when operationally practical.
- The downloader receives only certificate, proxy, path, and locale environment variables.
  Authenticated proxy variables are secrets and must be protected by the service manager.
- Samsung control requests use TLS, but upstream firmware clients may retrieve an encrypted
  payload over cleartext transport. The selected backend does not expose Bifrost's encrypted
  CRC32/Content-MD5 result. `jayspray` therefore validates the decrypted ZIP CRCs and records
  SHA-256, but that post-download hash is not an authenticated origin proof. Deploy on a
  trusted network and prefer a future backend that exposes Samsung's signed or authenticated
  integrity metadata.
- Extraction treats firmware ZIPs as untrusted input and enforces traversal, file-type,
  member-count, expanded-size, and compression-ratio limits.

## Public repository hygiene

Never commit firmware binaries, `.partial` downloads, SQLite databases, local configuration,
environment files, access tokens, cookies, authorization headers, proxy credentials, crash
dumps, or extracted device/customer data. Do not publish them in pull requests or other public
channels. The CI hygiene test checks for known local identity markers, and repository ignore
rules exclude common local state.
