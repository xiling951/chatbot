PRAGMA foreign_keys=ON;
PRAGMA secure_delete=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS identities(
 platform TEXT NOT NULL, bot TEXT NOT NULL, sender TEXT NOT NULL, person TEXT NOT NULL UNIQUE,
 PRIMARY KEY(platform,bot,sender));
CREATE TABLE IF NOT EXISTS scopes(
 scope TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS facts(
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, predicate TEXT NOT NULL, value TEXT NOT NULL,
 source TEXT NOT NULL, source_type TEXT NOT NULL, version INTEGER NOT NULL,
 status TEXT NOT NULL, supersedes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS current_fact ON facts(scope,predicate) WHERE status='active';
CREATE TABLE IF NOT EXISTS receipts(
 event TEXT PRIMARY KEY, state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns(
 id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, conversation TEXT NOT NULL,
 source TEXT NOT NULL, user_text TEXT NOT NULL, assistant_text TEXT NOT NULL,
 revision INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS scoped_turns ON turns(scope,conversation,id);
CREATE TABLE IF NOT EXISTS scope_metadata(
 scope TEXT PRIMARY KEY, persona TEXT NOT NULL, person TEXT NOT NULL, visibility TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_preferences(
 scope TEXT PRIMARY KEY, automatic INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS candidates(
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, predicate TEXT NOT NULL, value TEXT NOT NULL,
 source TEXT NOT NULL, revision INTEGER NOT NULL, base_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS scoped_candidates ON candidates(scope,predicate);
CREATE TABLE IF NOT EXISTS events(
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
 source TEXT NOT NULL, source_type TEXT NOT NULL, occurred_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE VIEW IF NOT EXISTS relationships AS
 SELECT f.*, s.persona, s.person, s.visibility FROM facts f
 JOIN scope_metadata s ON f.scope=s.scope
 WHERE f.status='active' AND f.predicate IN ('称呼','互动边界','称呼边界');
CREATE TABLE IF NOT EXISTS emotions(
 scope TEXT PRIMARY KEY, valence REAL NOT NULL, arousal REAL NOT NULL, social_energy REAL NOT NULL,
 updated_at TEXT NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS emotion_events(
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, source TEXT NOT NULL, reason TEXT NOT NULL,
 revision INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(scope,source));
CREATE TABLE IF NOT EXISTS proactive_policies(
 scope TEXT PRIMARY KEY, target TEXT NOT NULL, timezone TEXT NOT NULL,
 maximum_per_day INTEGER NOT NULL, quiet_start INTEGER NOT NULL, quiet_end INTEGER NOT NULL,
 auth_revision INTEGER NOT NULL, last_response_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, source TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 rule TEXT NOT NULL, timezone TEXT NOT NULL, next_run_at TEXT, expires_at TEXT NOT NULL,
 grace_seconds INTEGER NOT NULL, auth_revision INTEGER NOT NULL, memory_revision INTEGER NOT NULL,
 content TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(scope,idempotency_key));
CREATE INDEX IF NOT EXISTS due_tasks ON tasks(state,next_run_at);
CREATE TABLE IF NOT EXISTS executions(
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, scope TEXT NOT NULL, source TEXT NOT NULL,
 scheduled_at TEXT NOT NULL, expires_at TEXT NOT NULL, auth_revision INTEGER NOT NULL,
 memory_revision INTEGER NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
 lease_until TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id,scheduled_at));
CREATE TABLE IF NOT EXISTS outbox(
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL UNIQUE, scope TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, target TEXT NOT NULL, content TEXT NOT NULL,
 auth_revision INTEGER NOT NULL, memory_revision INTEGER NOT NULL, expires_at TEXT NOT NULL,
 state TEXT NOT NULL, lease_until TEXT, not_before TEXT NOT NULL, reason TEXT NOT NULL,
 device_observation TEXT NOT NULL DEFAULT 'not_observed', read_state TEXT NOT NULL DEFAULT 'unknown');
CREATE INDEX IF NOT EXISTS queued_outbox ON outbox(state,not_before);
PRAGMA user_version=5;
