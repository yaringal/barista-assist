CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bags (
    id TEXT PRIMARY KEY,
    slot TEXT NOT NULL CHECK(slot IN ('normal','decaf')),
    coffee_name TEXT NOT NULL,
    roaster TEXT,
    roast_date TEXT,
    opened_at TEXT NOT NULL,
    starting_mass_g REAL NOT NULL,
    dose_g REAL NOT NULL,
    grind REAL NOT NULL,
    target_yield_g REAL NOT NULL,
    temperature_offset_c INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_bags_active_slot ON bags(slot, active);

CREATE TABLE IF NOT EXISTS shots (
    id TEXT PRIMARY KEY,
    bag_id TEXT NOT NULL REFERENCES bags(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    dose_g REAL NOT NULL,
    grind REAL NOT NULL,
    target_yield_g REAL NOT NULL,
    actual_yield_g REAL,
    temperature_offset_c INTEGER NOT NULL,
    preinfusion_s REAL NOT NULL,
    stop_compensation_g REAL NOT NULL,
    stop_command_elapsed_ms INTEGER,
    status TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_shots_bag_time ON shots(bag_id, started_at DESC);

CREATE TABLE IF NOT EXISTS samples (
    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    scale_ms INTEGER NOT NULL,
    weight_g REAL NOT NULL,
    flow_g_s REAL NOT NULL,
    battery_percent INTEGER NOT NULL,
    PRIMARY KEY(shot_id, seq)
);
