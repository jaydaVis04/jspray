CREATE TABLE firmware_release (
    id TEXT PRIMARY KEY,
    weak_key TEXT NOT NULL,
    strong_key TEXT,
    model TEXT NOT NULL,
    sales_csc TEXT NOT NULL,
    device_name TEXT,
    country TEXT,
    region TEXT,
    carrier TEXT,
    ap_version TEXT NOT NULL,
    csc_version TEXT,
    cp_version TEXT,
    data_version TEXT,
    full_version TEXT,
    android_version TEXT,
    one_ui_version TEXT,
    security_patch TEXT,
    bootloader_revision TEXT,
    changelist TEXT,
    build_date TEXT,
    source_upload_date TEXT,
    source_updated_date TEXT,
    expected_size INTEGER CHECK (expected_size IS NULL OR expected_size >= 0),
    state TEXT NOT NULL DEFAULT 'DISCOVERED' CHECK (state IN (
        'DISCOVERED', 'RESOLVED', 'QUEUED', 'DOWNLOADING', 'DOWNLOADED',
        'VERIFIED', 'DECRYPTED', 'EXTRACTED', 'FAILED'
    )),
    first_discovered_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    state_updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX firmware_release_strong_identity_uq
    ON firmware_release(strong_key) WHERE strong_key IS NOT NULL;
CREATE INDEX firmware_release_weak_identity_idx ON firmware_release(weak_key);
CREATE INDEX firmware_release_lookup_idx ON firmware_release(model, ap_version, sales_csc);
CREATE INDEX firmware_release_state_idx ON firmware_release(state, first_discovered_at);

CREATE TABLE source_observation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firmware_release_id TEXT NOT NULL REFERENCES firmware_release(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_record_key TEXT NOT NULL,
    sales_csc TEXT NOT NULL,
    ap_version TEXT NOT NULL,
    full_version TEXT NOT NULL,
    source_url TEXT NOT NULL,
    detail_url TEXT,
    payload_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 1 CHECK (observation_count > 0),
    UNIQUE(source, source_record_key)
);

CREATE INDEX source_observation_release_idx
    ON source_observation(firmware_release_id, sales_csc, ap_version);

CREATE TABLE artifact (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firmware_release_id TEXT NOT NULL REFERENCES firmware_release(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('decrypted_zip', 'manifest', 'extracted_file')),
    path TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    crc32 TEXT,
    md5 TEXT,
    status TEXT NOT NULL CHECK (status IN ('PARTIAL', 'VERIFIED', 'CATALOGED')),
    created_at TEXT NOT NULL,
    UNIQUE(firmware_release_id, kind, path)
);

CREATE INDEX artifact_release_idx ON artifact(firmware_release_id, kind);

CREATE TABLE binary_blob (
    sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
    path TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL CHECK (size >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE run (
    id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    dry_run INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')),
    metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE failure (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
    firmware_release_id TEXT REFERENCES firmware_release(id) ON DELETE SET NULL,
    source TEXT,
    operation TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('RETRYABLE', 'PERMANENT')),
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 1 CHECK (attempts > 0),
    first_failed_at TEXT NOT NULL,
    last_failed_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX failure_unresolved_idx ON failure(resolved_at, operation, source);

CREATE TABLE watch_target (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    sales_csc TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    last_checked_at TEXT,
    last_success_at TEXT,
    last_version TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(model, sales_csc)
);

CREATE TABLE download_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firmware_release_id TEXT NOT NULL UNIQUE REFERENCES firmware_release(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
