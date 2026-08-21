CREATE TABLE firmware_target (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    sales_csc TEXT NOT NULL,
    device_name TEXT,
    country TEXT,
    region TEXT,
    carrier TEXT,
    latest_release_id TEXT REFERENCES firmware_release(id) ON DELETE SET NULL,
    latest_full_version TEXT,
    first_discovered_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    last_resolved_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(model, sales_csc)
);

CREATE TABLE target_observation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firmware_target_id TEXT NOT NULL REFERENCES firmware_target(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_record_key TEXT NOT NULL,
    source_url TEXT NOT NULL,
    detail_url TEXT,
    payload_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 1 CHECK (observation_count > 0),
    UNIQUE(source, source_record_key)
);

CREATE INDEX target_observation_target_idx
    ON target_observation(firmware_target_id, source);

CREATE UNIQUE INDEX firmware_release_model_csc_version_uq
    ON firmware_release(model, sales_csc, ap_version);
