-- CROWN INFINITY — schema 2: Bộ não điều hành (bộ nhớ quyết định, bản đồ mã, quyền Agent theo nhiệm vụ,
-- lượt rà soát bản build, lịch chạy).

-- Bộ nhớ quyết định theo dự án.
--   kind=DECISION  : quyết định đã được NGƯỜI xác nhận (confirmed_by bắt buộc).
--   kind=PROPOSAL  : đề xuất do AI/Agent viết, CHƯA phải quyết định cho tới khi OWNER xác nhận.
CREATE TABLE decisions (
  id               TEXT PRIMARY KEY,
  project_id       TEXT NOT NULL REFERENCES projects(id),
  kind             TEXT NOT NULL CHECK (kind IN ('DECISION','PROPOSAL')),
  status           TEXT NOT NULL CHECK (status IN ('ACTIVE','REVOKED','PROPOSED','REJECTED')),
  title            TEXT NOT NULL,
  content          TEXT NOT NULL,
  source           TEXT NOT NULL,
  decided_at       TEXT NOT NULL,
  confirmed_by     TEXT REFERENCES users(id),
  confirmed_at     TEXT,
  proposed_by_type TEXT NOT NULL CHECK (proposed_by_type IN ('USER','AGENT','SYSTEM')),
  proposed_by      TEXT NOT NULL,
  ai_model         TEXT NOT NULL DEFAULT '',
  revoked_at       TEXT,
  revoked_by       TEXT,
  revoke_reason    TEXT NOT NULL DEFAULT '',
  revision         INTEGER NOT NULL DEFAULT 1,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  CHECK (kind = 'PROPOSAL' OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)),
  CHECK ((kind = 'DECISION' AND status IN ('ACTIVE','REVOKED')) OR (kind = 'PROPOSAL' AND status IN ('PROPOSED','REJECTED')))
);
CREATE INDEX idx_decisions_project ON decisions(project_id, status);

CREATE TABLE decision_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id   TEXT NOT NULL REFERENCES decisions(id),
  revision      INTEGER NOT NULL,
  action        TEXT NOT NULL CHECK (action IN ('CREATE','EDIT','REVOKE','PROPOSE','CONFIRM','REJECT')),
  actor_type    TEXT NOT NULL,
  actor_id      TEXT NOT NULL,
  ts            TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  note          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_decision_history ON decision_history(decision_id, id);
CREATE TRIGGER decision_history_append_only BEFORE UPDATE ON decision_history
BEGIN SELECT RAISE(ABORT, 'decision_history is append-only'); END;
CREATE TRIGGER decision_history_no_delete BEFORE DELETE ON decision_history
BEGIN SELECT RAISE(ABORT, 'decision_history is append-only'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions cannot be deleted; revoke instead'); END;

-- Bản đồ mã (Graphify): snapshot bất biến gắn với đúng một commit nguồn.
CREATE TABLE graph_maps (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES projects(id),
  tool            TEXT NOT NULL,
  tool_version    TEXT NOT NULL,
  source_commit   TEXT NOT NULL,
  scope_prefix    TEXT NOT NULL,
  files_json      TEXT NOT NULL,
  node_count      INTEGER NOT NULL,
  edge_count      INTEGER NOT NULL,
  extracted_count INTEGER NOT NULL,
  inferred_count  INTEGER NOT NULL,
  graph_sha256    TEXT NOT NULL,
  notes           TEXT NOT NULL DEFAULT '',
  created_by      TEXT NOT NULL,
  created_at      TEXT NOT NULL
);
CREATE INDEX idx_graph_maps_project ON graph_maps(project_id, created_at);

CREATE TABLE graph_nodes (
  map_id      TEXT NOT NULL REFERENCES graph_maps(id),
  node_id     TEXT NOT NULL,
  label       TEXT NOT NULL,
  norm_label  TEXT NOT NULL,
  node_type   TEXT NOT NULL DEFAULT '',
  source_file TEXT NOT NULL DEFAULT '',
  source_line INTEGER,
  PRIMARY KEY (map_id, node_id)
);
CREATE INDEX idx_graph_nodes_label ON graph_nodes(map_id, norm_label);

CREATE TABLE graph_edges (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  map_id           TEXT NOT NULL REFERENCES graph_maps(id),
  source           TEXT NOT NULL,
  target           TEXT NOT NULL,
  relation         TEXT NOT NULL,
  confidence       TEXT NOT NULL CHECK (confidence IN ('EXTRACTED','INFERRED','AMBIGUOUS')),
  confidence_score REAL,
  source_file      TEXT NOT NULL DEFAULT '',
  source_line      INTEGER
);
CREATE INDEX idx_graph_edges_src ON graph_edges(map_id, source);
CREATE INDEX idx_graph_edges_dst ON graph_edges(map_id, target);
CREATE TRIGGER graph_edges_immutable BEFORE UPDATE ON graph_edges
BEGIN SELECT RAISE(ABORT, 'graph maps are immutable snapshots'); END;

-- Quyền của Agent: chỉ đúng MỘT dự án qua MỘT nhiệm vụ. Bắt đầu bằng quyền đọc.
CREATE TABLE agent_grants (
  id         TEXT PRIMARY KEY,
  agent_id   TEXT NOT NULL REFERENCES agent_identities(agent_id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  task_id    TEXT NOT NULL REFERENCES tasks(id),
  permission TEXT NOT NULL CHECK (permission IN ('READ','READ_PROPOSE')),
  granted_by TEXT NOT NULL,
  granted_at TEXT NOT NULL,
  revoked_at TEXT,
  revoked_by TEXT
);
CREATE INDEX idx_agent_grants ON agent_grants(agent_id, task_id);
CREATE TRIGGER agent_grant_task_in_project BEFORE INSERT ON agent_grants
WHEN NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id = NEW.task_id AND t.project_id = NEW.project_id)
BEGIN SELECT RAISE(ABORT, 'task does not belong to project'); END;

-- Lượt rà soát bản build do Agent Vua thực hiện.
CREATE TABLE review_runs (
  id                TEXT PRIMARY KEY,
  task_id           TEXT REFERENCES tasks(id),
  project_id        TEXT NOT NULL REFERENCES projects(id),
  agent_id          TEXT NOT NULL,
  trigger           TEXT NOT NULL CHECK (trigger IN ('MANUAL','SCHEDULE')),
  requested_by      TEXT NOT NULL,
  status            TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','SKIPPED_DUPLICATE')),
  input_fingerprint TEXT NOT NULL,
  inputs_json       TEXT NOT NULL,
  duplicate_of      TEXT REFERENCES review_runs(id),
  steps_json        TEXT NOT NULL DEFAULT '[]',
  report_json       TEXT NOT NULL DEFAULT '{}',
  report_md         TEXT NOT NULL DEFAULT '',
  ai_calls          INTEGER NOT NULL DEFAULT 0,
  error             TEXT NOT NULL DEFAULT '',
  created_at        TEXT NOT NULL,
  finished_at       TEXT
);
CREATE INDEX idx_review_runs_project ON review_runs(project_id, created_at);
-- Cùng dự án + cùng đầu vào: chỉ một lượt đang chạy hoặc đã thành công.
CREATE UNIQUE INDEX idx_review_runs_dedup ON review_runs(project_id, input_fingerprint)
  WHERE status IN ('RUNNING','SUCCEEDED');

CREATE TABLE schedules (
  id               TEXT PRIMARY KEY,
  project_id       TEXT NOT NULL REFERENCES projects(id),
  kind             TEXT NOT NULL CHECK (kind IN ('BUILD_REVIEW')),
  interval_minutes INTEGER NOT NULL DEFAULT 120 CHECK (interval_minutes >= 30),
  enabled          INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
  config_json      TEXT NOT NULL DEFAULT '{}',
  next_run_at      TEXT,
  last_run_at      TEXT,
  last_run_id      TEXT,
  last_status      TEXT,
  updated_at       TEXT NOT NULL,
  updated_by       TEXT NOT NULL,
  UNIQUE (project_id, kind)
);
