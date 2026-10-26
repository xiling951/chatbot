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
PRAGMA user_version=3;
