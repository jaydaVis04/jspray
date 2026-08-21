CREATE TABLE external_metadata_model (
    path TEXT NOT NULL,
    model TEXT NOT NULL,
    first_seen_offset INTEGER NOT NULL CHECK (first_seen_offset >= 0),
    PRIMARY KEY(path, model)
);

CREATE TABLE external_metadata_state (
    path TEXT PRIMARY KEY,
    device INTEGER NOT NULL,
    inode INTEGER NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    mtime_ns INTEGER NOT NULL,
    indexed_offset INTEGER NOT NULL CHECK (indexed_offset >= 0),
    updated_at TEXT NOT NULL
);
