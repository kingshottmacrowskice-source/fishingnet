ALTER TABLE workspace_settings ADD COLUMN simple_mode boolean NOT NULL DEFAULT true;
ALTER TABLE discovery_sources ADD COLUMN daily_time text;
ALTER TABLE discovery_sources ADD COLUMN schedule_timezone text NOT NULL DEFAULT 'Asia/Shanghai';
ALTER TABLE discovery_sources ADD CONSTRAINT valid_daily_time CHECK(daily_time IS NULL OR daily_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$');
CREATE TABLE video_summaries (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 video_key text NOT NULL, source_id uuid NOT NULL, version_id uuid NOT NULL,
 title text NOT NULL, summary text NOT NULL, points jsonb NOT NULL DEFAULT '[]',
 entities jsonb NOT NULL DEFAULT '[]', model_runs jsonb NOT NULL DEFAULT '[]',
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,video_key), UNIQUE(workspace_id,id),
 FOREIGN KEY(workspace_id,source_id) REFERENCES sources(workspace_id,id),
 FOREIGN KEY(workspace_id,version_id) REFERENCES source_versions(workspace_id,id)
);
CREATE TABLE summary_rounds (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 discovery_run_id uuid, origin text NOT NULL CHECK(origin IN ('keywords','mentions','manual')),
 state text NOT NULL DEFAULT 'collecting' CHECK(state IN ('collecting','queued','running','succeeded','partial','failed')),
 job_id uuid, report text NOT NULL DEFAULT '', error_code text, created_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz, UNIQUE(workspace_id,id), UNIQUE(workspace_id,discovery_run_id),
 FOREIGN KEY(workspace_id,discovery_run_id) REFERENCES discovery_runs(workspace_id,id),
 FOREIGN KEY(workspace_id,job_id) REFERENCES jobs(workspace_id,id)
);
CREATE TABLE summary_round_items (
 workspace_id uuid NOT NULL, round_id uuid NOT NULL, video_key text NOT NULL,
 source_id uuid NOT NULL, collect_job_id uuid,
 PRIMARY KEY(round_id,video_key),
 FOREIGN KEY(workspace_id,round_id) REFERENCES summary_rounds(workspace_id,id),
 FOREIGN KEY(workspace_id,source_id) REFERENCES sources(workspace_id,id),
 FOREIGN KEY(workspace_id,collect_job_id) REFERENCES jobs(workspace_id,id)
);
CREATE TABLE summary_entity_history (
 workspace_id uuid NOT NULL, round_id uuid NOT NULL, node_id uuid NOT NULL,
 description text NOT NULL, summary_ids jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(round_id,node_id),
 FOREIGN KEY(workspace_id,round_id) REFERENCES summary_rounds(workspace_id,id),
 FOREIGN KEY(workspace_id,node_id) REFERENCES nodes(workspace_id,id)
);
CREATE TABLE mention_monitors (
 workspace_id uuid PRIMARY KEY REFERENCES workspace_settings(id),
 enabled boolean NOT NULL DEFAULT false, network_authorized boolean NOT NULL DEFAULT false,
 interval_minutes int NOT NULL DEFAULT 30 CHECK(interval_minutes BETWEEN 5 AND 10080),
 next_run_at timestamptz, last_checked_at timestamptz, error_code text,
 cursor_data jsonb NOT NULL DEFAULT '{}', active_job_id uuid,
 FOREIGN KEY(workspace_id,active_job_id) REFERENCES jobs(workspace_id,id)
);
CREATE TABLE mention_receipts (
 workspace_id uuid NOT NULL REFERENCES workspace_settings(id), notification_id text NOT NULL,
 video_key text, status text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(workspace_id,notification_id)
);
CREATE INDEX summary_rounds_pending ON summary_rounds(state,created_at);
CREATE INDEX video_summaries_feed ON video_summaries(workspace_id,created_at DESC,id DESC);
