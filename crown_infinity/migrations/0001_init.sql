-- CROWN INFINITY — lược đồ khởi tạo (schema 1).
-- Quy ước: thời gian ISO-8601 UTC dạng 2026-11-06T12:00:00.000Z; ID dạng tiền tố + hex ngẫu nhiên.

CREATE TABLE users (
  id            TEXT PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name  TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('OWNER','EDITOR','VIEWER')),
  is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE sessions (
  token_hash   TEXT PRIMARY KEY,           -- sha256 của token; token gốc chỉ nằm trong cookie
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf_token   TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  revoked_at   TEXT,
  user_agent   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_sessions_user ON sessions(user_id);

CREATE TABLE projects (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_by  TEXT REFERENCES users(id),
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  archived_at TEXT
);

CREATE TABLE project_members (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role       TEXT NOT NULL CHECK (role IN ('EDITOR','VIEWER')),
  added_at   TEXT NOT NULL,
  PRIMARY KEY (project_id, user_id)
);

CREATE TABLE modules (
  module_id     TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  version       TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  dev_status    TEXT NOT NULL CHECK (dev_status IN ('PLANNED','IMPLEMENTED','VERIFIED','BLOCKED')),
  enabled       INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
  updated_at    TEXT NOT NULL,
  updated_by    TEXT
);

CREATE TABLE tools (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  category     TEXT NOT NULL DEFAULT '',
  version      TEXT,
  source       TEXT NOT NULL CHECK (source IN ('tool-catalog','manual')),
  source_ref   TEXT NOT NULL DEFAULT '',
  details_json TEXT NOT NULL DEFAULT '{}',
  synced_at    TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE tasks (
  id          TEXT PRIMARY KEY,
  project_id  TEXT REFERENCES projects(id),
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL CHECK (status IN ('TODO','IN_PROGRESS','BLOCKED','DONE','CANCELLED')),
  assignee    TEXT NOT NULL DEFAULT '',
  created_by  TEXT REFERENCES users(id),
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  revision    INTEGER NOT NULL DEFAULT 1
);

-- Nhật ký chỉ thêm: không chứa secret và không chép nội dung prompt.
CREATE TABLE activity_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  actor_type  TEXT NOT NULL CHECK (actor_type IN ('USER','AGENT','SYSTEM','ANONYMOUS')),
  actor_id    TEXT,
  action      TEXT NOT NULL,
  object_type TEXT,
  object_id   TEXT,
  result      TEXT NOT NULL CHECK (result IN ('OK','DENIED','ERROR')),
  detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_activity_ts ON activity_logs(ts);
CREATE TRIGGER activity_logs_append_only BEFORE UPDATE ON activity_logs
BEGIN SELECT RAISE(ABORT, 'activity_logs is append-only'); END;
CREATE TRIGGER activity_logs_no_delete BEFORE DELETE ON activity_logs
BEGIN SELECT RAISE(ABORT, 'activity_logs is append-only'); END;

CREATE TABLE settings (
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by TEXT
);

-- Khóa API KHÔNG nằm ở đây: key_env là TÊN biến môi trường phía máy chủ.
CREATE TABLE ai_providers (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  kind       TEXT NOT NULL,
  base_url   TEXT NOT NULL DEFAULT '',
  model      TEXT NOT NULL DEFAULT '',
  key_env    TEXT NOT NULL DEFAULT '',
  paid       INTEGER NOT NULL DEFAULT 1 CHECK (paid IN (0,1)),
  enabled    INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE ai_usage (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                TEXT NOT NULL,
  provider_id       TEXT,
  model             TEXT,
  purpose           TEXT NOT NULL DEFAULT '',
  prompt_version_id TEXT,
  status            TEXT NOT NULL CHECK (status IN ('RESERVED','SUCCEEDED','FAILED','RELEASED')),
  reserved_usd      REAL NOT NULL DEFAULT 0,
  cost_usd          REAL,
  cost_is_estimate  INTEGER NOT NULL DEFAULT 1,
  input_tokens      INTEGER,
  output_tokens     INTEGER,
  latency_ms        INTEGER,
  error             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_ai_usage_ts ON ai_usage(ts);

CREATE TABLE prompts (
  id                    TEXT PRIMARY KEY,
  project_id            TEXT NOT NULL REFERENCES projects(id),
  title                 TEXT NOT NULL,
  description           TEXT NOT NULL DEFAULT '',
  category              TEXT NOT NULL DEFAULT '',
  target_ai             TEXT NOT NULL DEFAULT '',
  author_id             TEXT REFERENCES users(id),
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  archived_at           TEXT,
  favorite              INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0,1)),
  revision              INTEGER NOT NULL DEFAULT 1,
  draft_content         TEXT NOT NULL DEFAULT '',
  draft_updated_at      TEXT,
  draft_updated_by      TEXT,
  head_version_id       TEXT REFERENCES prompt_versions(id),
  production_version_id TEXT REFERENCES prompt_versions(id)
);
CREATE INDEX idx_prompts_project ON prompts(project_id);

-- Snapshot bất biến: nội dung, số phiên bản, checksum không sửa và không xóa được.
CREATE TABLE prompt_versions (
  id            TEXT PRIMARY KEY,
  prompt_id     TEXT NOT NULL REFERENCES prompts(id),
  version_no    INTEGER NOT NULL,
  content       TEXT NOT NULL,
  checksum      TEXT NOT NULL,
  changelog     TEXT NOT NULL DEFAULT '',
  author_id     TEXT REFERENCES users(id),
  created_at    TEXT NOT NULL,
  restored_from TEXT REFERENCES prompt_versions(id),
  review_status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (review_status IN ('DRAFT','TESTING','APPROVED','REJECTED')),
  reviewed_by   TEXT,
  reviewed_at   TEXT,
  review_note   TEXT NOT NULL DEFAULT '',
  UNIQUE (prompt_id, version_no)
);
CREATE TRIGGER prompt_versions_immutable
BEFORE UPDATE OF prompt_id, version_no, content, checksum, author_id, created_at, restored_from ON prompt_versions
BEGIN SELECT RAISE(ABORT, 'prompt_versions content is immutable'); END;
CREATE TRIGGER prompt_versions_no_delete BEFORE DELETE ON prompt_versions
BEGIN SELECT RAISE(ABORT, 'prompt_versions cannot be deleted'); END;

-- Con trỏ production chỉ được trỏ tới phiên bản APPROVED của chính prompt đó.
CREATE TRIGGER prompts_production_must_be_approved
BEFORE UPDATE OF production_version_id ON prompts
WHEN NEW.production_version_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM prompt_versions v
  WHERE v.id = NEW.production_version_id AND v.prompt_id = NEW.id AND v.review_status = 'APPROVED')
BEGIN SELECT RAISE(ABORT, 'production version must be an APPROVED version of this prompt'); END;

CREATE TABLE tags (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE prompt_tags (
  prompt_id TEXT NOT NULL REFERENCES prompts(id),
  tag_id    INTEGER NOT NULL REFERENCES tags(id),
  PRIMARY KEY (prompt_id, tag_id)
);

-- Nguồn tham chiếu là DỮ LIỆU, không phải chỉ thị; URL không được tự truy cập.
CREATE TABLE sources (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('URL','NOTE','DOCUMENT','REPOSITORY','ISSUE','TEST_RESULT','GOAL')),
  title       TEXT NOT NULL,
  url         TEXT NOT NULL DEFAULT '',
  note        TEXT NOT NULL DEFAULT '',
  captured_at TEXT NOT NULL,
  created_by  TEXT REFERENCES users(id),
  created_at  TEXT NOT NULL
);
CREATE TABLE prompt_sources (
  prompt_id TEXT NOT NULL REFERENCES prompts(id),
  source_id TEXT NOT NULL REFERENCES sources(id),
  linked_at TEXT NOT NULL,
  linked_by TEXT,
  PRIMARY KEY (prompt_id, source_id)
);

-- Danh tính Agent: token gốc chỉ hiển thị một lần; ở đây chỉ lưu băm.
CREATE TABLE agent_identities (
  agent_id        TEXT PRIMARY KEY,
  display_name    TEXT NOT NULL,
  source          TEXT NOT NULL DEFAULT 'manual',
  token_hash      TEXT UNIQUE,
  token_hint      TEXT NOT NULL DEFAULT '',
  token_issued_at TEXT,
  is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  last_used_at    TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE agent_prompt_assignments (
  id          TEXT PRIMARY KEY,
  agent_id    TEXT NOT NULL REFERENCES agent_identities(agent_id),
  prompt_id   TEXT NOT NULL REFERENCES prompts(id),
  version_id  TEXT NOT NULL REFERENCES prompt_versions(id),
  permission  TEXT NOT NULL DEFAULT 'READ' CHECK (permission IN ('READ')),
  note        TEXT NOT NULL DEFAULT '',
  assigned_by TEXT NOT NULL,
  assigned_at TEXT NOT NULL,
  revoked_at  TEXT,
  revoked_by  TEXT
);
CREATE UNIQUE INDEX idx_assign_active ON agent_prompt_assignments(agent_id, prompt_id) WHERE revoked_at IS NULL;
CREATE TRIGGER assignment_version_matches_prompt BEFORE INSERT ON agent_prompt_assignments
WHEN NOT EXISTS (SELECT 1 FROM prompt_versions v WHERE v.id = NEW.version_id AND v.prompt_id = NEW.prompt_id)
BEGIN SELECT RAISE(ABORT, 'version does not belong to prompt'); END;
CREATE TRIGGER assignment_pinned BEFORE UPDATE OF agent_id, prompt_id, version_id ON agent_prompt_assignments
BEGIN SELECT RAISE(ABORT, 'assignment is pinned; revoke and create a new one'); END;

-- Chỉ mục tìm kiếm là dữ liệu dẫn xuất (dựng lại được): văn bản đã gấp dấu.
CREATE TABLE search_docs (
  prompt_id TEXT PRIMARY KEY REFERENCES prompts(id),
  title_f   TEXT NOT NULL,
  body_f    TEXT NOT NULL,
  tags_f    TEXT NOT NULL
);
