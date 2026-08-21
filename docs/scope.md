# Current project scope

Last changed: 2026-08-21

JAYSPRAY is a Linux-only headless workflow for recent Samsung firmware:

- read model and region/CSC pairs from public SamFrew, SamMobile, and ordinarily accessible
  SamFW newest-release indexes;
- accept only dated records from the last 21 days by default;
- skip a model already present in an optional large external metadata file;
- choose one observed region per new model and resolve Samsung's latest official version;
- download and decrypt through Samsung FUS using a Bifrost-compatible headless backend;
- verify, extract, inventory, and catalog without flashing a device.

Index PDA values are outside discovery and duplicate policy. Model-level metadata exclusion
prevents all regions of an already cataloged model from downloading. Model/region remains the
Samsung request and provenance identity; the exact AP/CSC/CP tuple returned by Samsung is
stored only to make the official request reproducible. SHA-256 detects identical payloads.

Indexes are metadata-only. Website downloads, credentials, GUI automation, CAPTCHA solving,
paywall bypass, anti-bot evasion, and device flashing are out of scope. SamFW remains disabled
while its public page denies ordinary HTTP access.

External metadata scanning is format-tolerant and cached. Automatic mutation is opt-in and
currently limited to a validated JSONL record shape. A different supplied schema requires a
matching writer before append is enabled.
