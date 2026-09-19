PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  name TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_run_kind ON artifacts(run_id, kind);
