CREATE TABLE source_checkpoint (
    source TEXT PRIMARY KEY,
    last_checked_at TEXT NOT NULL,
    last_success_at TEXT,
    latest_record_key TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);
