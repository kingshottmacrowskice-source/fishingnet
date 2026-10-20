-- M2 Phase 2: keyword discovery sources, run ledger, per-candidate decisions
-- and durable job checkpoints for cross-stage recovery.
--
-- Additive only: the six core tables keep their meaning. Discovery configuration
-- never stores content, credentials or derived facts; it stores bounded user
-- configuration, run counters and per-candidate decisions (including why a
-- candidate was excluded), so a run can be explained after the fact.
CREATE TABLE discovery_sources (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 platform text NOT NULL DEFAULT 'bilibili',
 name text NOT NULL,
 keywords jsonb NOT NULL,
 exclude_terms jsonb NOT NULL DEFAULT '[]',
 languages jsonb NOT NULL DEFAULT '["zh-CN","zh-Hans"]',
 match_mode text NOT NULL DEFAULT 'any' CHECK(match_mode IN ('any','all')),
 language_policy text NOT NULL DEFAULT 'prefer' CHECK(language_policy IN ('prefer','require')),
 page_policy text NOT NULL DEFAULT 'first' CHECK(page_policy IN ('first','explicit')),
 target_pages jsonb NOT NULL DEFAULT '[]',
 max_results_per_run int NOT NULL DEFAULT 10 CHECK(max_results_per_run BETWEEN 1 AND 20),
 max_pages_per_run int NOT NULL DEFAULT 2 CHECK(max_pages_per_run BETWEEN 1 AND 3),
 max_requests_per_run int NOT NULL DEFAULT 3 CHECK(max_requests_per_run BETWEEN 1 AND 4),
 rescan_after_hours int NOT NULL DEFAULT 168 CHECK(rescan_after_hours BETWEEN 0 AND 8760),
 schedule_interval_minutes int NOT NULL DEFAULT 1440 CHECK(schedule_interval_minutes BETWEEN 60 AND 10080),
 enabled boolean NOT NULL DEFAULT false,
 network_authorized boolean NOT NULL DEFAULT false,
 next_run_at timestamptz,
 last_run_at timestamptz,
 last_success_at timestamptz,
 last_error_code text,
 failure_streak int NOT NULL DEFAULT 0 CHECK(failure_streak >= 0),
 watermark jsonb NOT NULL DEFAULT '{"schema_version":"watermark.v1"}',
 deleted_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 revision int NOT NULL DEFAULT 1,
 UNIQUE(workspace_id,id),
 UNIQUE(workspace_id,name),
 CHECK(jsonb_typeof(keywords)='array' AND jsonb_typeof(exclude_terms)='array'
       AND jsonb_typeof(languages)='array' AND jsonb_typeof(target_pages)='array'),
 CHECK(jsonb_array_length(keywords) BETWEEN 1 AND 5),
 CHECK(jsonb_array_length(exclude_terms) <= 20),
 CHECK(jsonb_array_length(target_pages) <= 3)
);
CREATE INDEX discovery_sources_due ON discovery_sources(workspace_id,next_run_at)
 WHERE enabled AND deleted_at IS NULL;

CREATE TABLE discovery_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL,
 source_id uuid NOT NULL,
 trigger text NOT NULL CHECK(trigger IN ('manual','schedule','retry')),
 state text NOT NULL DEFAULT 'queued'
   CHECK(state IN ('queued','running','succeeded','partial','failed','cancelled','blocked_credentials','blocked_upstream')),
 requested_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,
 finished_at timestamptz,
 request_budget int NOT NULL DEFAULT 3 CHECK(request_budget > 0),
 requests_used int NOT NULL DEFAULT 0 CHECK(requests_used >= 0),
 pages_visited int NOT NULL DEFAULT 0 CHECK(pages_visited >= 0),
 discovered int NOT NULL DEFAULT 0,
 matched int NOT NULL DEFAULT 0,
 excluded int NOT NULL DEFAULT 0,
 duplicates int NOT NULL DEFAULT 0,
 enqueued int NOT NULL DEFAULT 0,
 error_code text,
 detail text NOT NULL DEFAULT '',
 job_id uuid,
 UNIQUE(workspace_id,id),
 FOREIGN KEY(workspace_id,source_id) REFERENCES discovery_sources(workspace_id,id),
 FOREIGN KEY(workspace_id,job_id) REFERENCES jobs(workspace_id,id)
);
CREATE INDEX discovery_runs_source ON discovery_runs(workspace_id,source_id,requested_at DESC);

CREATE TABLE discovery_run_items (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL,
 run_id uuid NOT NULL,
 external_id text NOT NULL,
 bvid text NOT NULL DEFAULT '',
 page int NOT NULL DEFAULT 1 CHECK(page BETWEEN 1 AND 999),
 title text NOT NULL DEFAULT '',
 author_name text,
 published_at timestamptz,
 decision text NOT NULL CHECK(decision IN
   ('enqueued','refreshed','excluded_term','keyword_miss','duplicate','over_limit','skipped_deleted')),
 reason text NOT NULL DEFAULT '',
 source_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,run_id,external_id),
 FOREIGN KEY(workspace_id,run_id) REFERENCES discovery_runs(workspace_id,id),
 FOREIGN KEY(workspace_id,source_id) REFERENCES sources(workspace_id,id)
);
CREATE INDEX discovery_run_items_run ON discovery_run_items(workspace_id,run_id);

ALTER TABLE jobs ADD COLUMN checkpoint jsonb NOT NULL DEFAULT '{}';
ALTER TABLE sources ADD COLUMN discovery_source_id uuid;
ALTER TABLE sources ADD CONSTRAINT sources_discovery_source
 FOREIGN KEY(workspace_id,discovery_source_id) REFERENCES discovery_sources(workspace_id,id);
CREATE INDEX sources_discovery ON sources(workspace_id,discovery_source_id)
 WHERE discovery_source_id IS NOT NULL;
