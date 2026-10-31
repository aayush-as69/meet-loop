PRAGMA foreign_keys = ON;

-- One row per pasted/dictated transcript.
CREATE TABLE IF NOT EXISTS meetings (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  title              TEXT NOT NULL DEFAULT 'Untitled meeting',
  meeting_date       TEXT,                       -- ISO date (YYYY-MM-DD); NULL if not supplied
  raw_transcript     TEXT NOT NULL,              -- exactly what the user pasted
  cleaned_transcript TEXT NOT NULL,              -- fillers / self-corrections removed
  cleaning_log       TEXT NOT NULL DEFAULT '[]', -- JSON: every self-correction we applied
  extractor          TEXT NOT NULL DEFAULT 'rules' CHECK (extractor IN ('llm','rules')),
  fallback_reason    TEXT,                       -- why the LLM wasn't used, if it wasn't
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Decisions and action items share one table, split by `kind`.
CREATE TABLE IF NOT EXISTS items (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id     INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  position       INTEGER NOT NULL,               -- order of appearance in the transcript
  kind           TEXT NOT NULL CHECK (kind IN ('decision','action_item')),
  description    TEXT NOT NULL,
  topic          TEXT,                           -- decisions only: short lowercase label, e.g. 'database'
  owner          TEXT,                           -- NULL = unknown. Never guessed.
  deadline       TEXT,                           -- ISO date; NULL = unknown/unresolvable. Never guessed.
  deadline_raw   TEXT,                           -- what was actually said ("next Friday")
  deadline_note  TEXT,                           -- how it was interpreted, if there was a judgment call
  speaker        TEXT,                           -- who said it
  source_text    TEXT NOT NULL,                  -- original, unedited utterance (for audit)
  status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','dropped')),
  needs_review   INTEGER NOT NULL DEFAULT 0 CHECK (needs_review IN (0,1)),
  review_reasons TEXT NOT NULL DEFAULT '[]',     -- JSON: [{"code": "...", "message": "..."}]
  -- Stage 3 (review + GitHub). `status` above is an older, unused lifecycle column, so the person's
  -- decision lives in review_status; the API exposes it as `status`.
  review_status  TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
  issue_number   INTEGER,                        -- set once a GitHub issue exists; never posted twice
  issue_url      TEXT,
  post_claimed_at TEXT,                          -- UTC time a confirm started posting this item (double-click guard)
  -- Stage 4 (reminders + digest). Local time, no zone: 'YYYY-MM-DDTHH:MM:SS'. NULL = not done.
  done_at        TEXT,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_items_meeting ON items(meeting_id, position);
CREATE INDEX IF NOT EXISTS idx_items_review  ON items(needs_review) WHERE needs_review = 1;

-- A new decision that contradicts or overrides an earlier one (found by conflicts.py).
-- One row per new decision at most: the check returns a single conflicting decision.
CREATE TABLE IF NOT EXISTS conflicts (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id             INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,  -- the new decision
  conflicting_item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,  -- the earlier one it contradicts
  reason              TEXT NOT NULL,
  severity            TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  UNIQUE (item_id, conflicting_item_id)
);
CREATE INDEX IF NOT EXISTS idx_conflicts_item ON conflicts(item_id);

-- Stage 4: one row per reminder that has fired. UNIQUE(item_id, kind) is what guarantees a
-- reminder is never logged (and so never sent) twice, however often the job runs.
CREATE TABLE IF NOT EXISTS reminders (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kind     TEXT NOT NULL CHECK (kind IN ('h24','h1','overdue')),
  fired_at TEXT NOT NULL,                        -- the clock the job ran with (simulated in demos)
  UNIQUE (item_id, kind)
);
