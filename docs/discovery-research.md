# Firmware index research

Research date: 2026-08-21

JAYSPRAY needs a global newest-release signal because neither Bifrost nor Samsung's history
service enumerates all models and CSCs. Indexes are used only for metadata; Samsung remains
the preferred firmware payload source.

## SamFrew

- `/firmware` returns server-rendered HTML with ten newest rows and embedded Next.js data.
- The feed is sorted by upload date descending. Pagination uses
  `/firmware/upload/Desc/<offset>/10`.
- Rows expose model, CSC, PDA, CSC build, Android version, build/upload date, changelist, and
  a stable detail path.
- No login or JavaScript browser is required for the public newest rows. Its robots file
  permits this path. JAYSPRAY defaults to one page per daily run.

## SamMobile

- `/firmwares/` returns static HTML with a semantic “Latest Firmware” table.
- Rows expose model, CSC in the detail URL, country/carrier, upload date, Android version,
  PDA, and a stable numeric record identifier.
- No login or JavaScript browser is required to read the latest table. Download accounts are
  irrelevant because JAYSPRAY retrieves payloads from Samsung. Its robots file permits the
  firmware listing.

## SamFW

- Current ordinary HTTP requests to its home and firmware pages return HTTP 403.
- JAYSPRAY does not automate a browser, solve a CAPTCHA, or evade that restriction.
- An isolated adapter name is reserved, but it is disabled in the example configuration and
  reports an explicit source failure if deliberately enabled.

## Selection conclusion

The live feeds repeat models across many country and carrier CSCs. JAYSPRAY extracts model,
region/CSC, date, and provenance, but does not use the advertised PDA for selection. It keeps
model/region targets, applies the 21-day recency window, excludes any model already present
in the external metadata catalog, and selects one region per remaining model. Samsung then
returns the authoritative latest version for that request. Payload SHA-256 provides a final
binary duplicate boundary.
