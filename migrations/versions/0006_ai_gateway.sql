-- M3 Phase 1: model providers, model profiles, task routing, invocation ledger
-- and a pre-call budget reservation ledger.
--
-- Additive only. The six core tables keep their meaning. Provider credentials are
-- sealed with the deployment master key and only a masked hint is readable; a
-- model profile never stores a key. Every paid call reserves a worst-case amount
-- before the request and settles the actual amount afterwards, so a crash or an
-- unknown usage can never silently exceed the workspace budget.
ALTER TABLE workspace_settings ADD COLUMN budget_currency text NOT NULL DEFAULT 'USD'
 CHECK(budget_currency IN ('USD','CNY'));
ALTER TABLE workspace_settings ADD COLUMN routing_revision int NOT NULL DEFAULT 1
 CHECK(routing_revision > 0);

CREATE TABLE provider_connections (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 name text NOT NULL,
 protocol text NOT NULL CHECK(protocol IN ('openai','openai_compatible','local','mock')),
 base_url text NOT NULL DEFAULT '',
 is_local boolean NOT NULL DEFAULT false,
 credential_sealed text,
 credential_hint text NOT NULL DEFAULT '',
 status text NOT NULL DEFAULT 'unverified'
   CHECK(status IN ('unverified','ok','auth_failed','network_failed','capability_mismatch','disabled')),
 last_tested_at timestamptz,
 last_error_code text,
 last_test_detail text NOT NULL DEFAULT '',
 enabled boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 revision int NOT NULL DEFAULT 1,
 UNIQUE(workspace_id,id),
 UNIQUE(workspace_id,name),
 CHECK(protocol='mock' OR base_url ~ '^https?://[^[:space:]@]+'),
 CHECK(NOT is_local OR protocol IN ('local','mock'))
);
CREATE INDEX provider_connections_list ON provider_connections(workspace_id,name);

CREATE TABLE model_profiles (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL,
 provider_id uuid NOT NULL,
 model_id text NOT NULL,
 display_name text NOT NULL,
 tier text NOT NULL DEFAULT 'standard' CHECK(tier IN ('economy','standard','high')),
 context_window int NOT NULL DEFAULT 8192 CHECK(context_window BETWEEN 512 AND 2000000),
 max_output_tokens int NOT NULL DEFAULT 1024 CHECK(max_output_tokens BETWEEN 1 AND 200000),
 supports_structured_output boolean NOT NULL DEFAULT false,
 input_price_per_mtok numeric(20,8) NOT NULL DEFAULT 0 CHECK(input_price_per_mtok >= 0),
 output_price_per_mtok numeric(20,8) NOT NULL DEFAULT 0 CHECK(output_price_per_mtok >= 0),
 price_currency text NOT NULL DEFAULT 'USD' CHECK(price_currency IN ('USD','CNY')),
 price_version text NOT NULL DEFAULT 'v1',
 enabled boolean NOT NULL DEFAULT false,
 metadata jsonb NOT NULL DEFAULT '{"schema_version":"model-profile.v1"}',
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 revision int NOT NULL DEFAULT 1,
 UNIQUE(workspace_id,id),
 UNIQUE(workspace_id,provider_id,model_id),
 FOREIGN KEY(workspace_id,provider_id) REFERENCES provider_connections(workspace_id,id)
);
CREATE INDEX model_profiles_provider ON model_profiles(workspace_id,provider_id);

CREATE TABLE task_routes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL,
 task text NOT NULL,
 mode text NOT NULL DEFAULT 'mock' CHECK(mode IN ('mock','rule','model')),
 primary_model_id uuid,
 fallback_model_ids jsonb NOT NULL DEFAULT '[]',
 max_input_tokens int NOT NULL DEFAULT 8000 CHECK(max_input_tokens BETWEEN 1 AND 200000),
 max_output_tokens int NOT NULL DEFAULT 1024 CHECK(max_output_tokens BETWEEN 1 AND 200000),
 timeout_seconds int NOT NULL DEFAULT 30 CHECK(timeout_seconds BETWEEN 1 AND 300),
 max_attempts int NOT NULL DEFAULT 1 CHECK(max_attempts BETWEEN 1 AND 3),
 data_policy text NOT NULL DEFAULT 'local_only' CHECK(data_policy IN ('local_only','approved_cloud')),
 enabled boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 revision int NOT NULL DEFAULT 1,
 UNIQUE(workspace_id,id),
 UNIQUE(workspace_id,task),
 FOREIGN KEY(workspace_id,primary_model_id) REFERENCES model_profiles(workspace_id,id),
 CHECK(jsonb_typeof(fallback_model_ids)='array'),
 CHECK(jsonb_array_length(fallback_model_ids) <= 3),
 CHECK(mode <> 'model' OR primary_model_id IS NOT NULL)
);

CREATE FUNCTION model_profile_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM task_routes WHERE workspace_id=OLD.workspace_id AND primary_model_id=OLD.id) THEN
  RAISE EXCEPTION 'model profile is a primary route';
 END IF;
 IF EXISTS(SELECT 1 FROM task_routes t WHERE t.workspace_id=OLD.workspace_id
           AND t.fallback_model_ids ? OLD.id::text) THEN
  RAISE EXCEPTION 'model profile is a fallback route';
 END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER model_profile_in_use BEFORE DELETE ON model_profiles FOR EACH ROW EXECUTE FUNCTION model_profile_guard();

CREATE TABLE ai_invocations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 task text NOT NULL,
 status text NOT NULL DEFAULT 'running' CHECK(status IN ('running','succeeded','failed','blocked')),
 job_id uuid,
 provider_id uuid,
 model_profile_id uuid,
 provider_name text NOT NULL DEFAULT '',
 model_label text NOT NULL DEFAULT '',
 request_fingerprint text NOT NULL,
 input_summary jsonb NOT NULL DEFAULT '{}',
 config_snapshot jsonb NOT NULL,
 attempts int NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 attempt_log jsonb NOT NULL DEFAULT '[]',
 input_tokens int NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
 output_tokens int NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
 cached_tokens int NOT NULL DEFAULT 0 CHECK(cached_tokens >= 0),
 cost_estimate numeric(20,8) NOT NULL DEFAULT 0 CHECK(cost_estimate >= 0),
 cost_settled numeric(20,8),
 cost_status text NOT NULL DEFAULT 'none' CHECK(cost_status IN ('none','estimated','confirmed','unknown')),
 currency text NOT NULL DEFAULT 'USD',
 provider_request_id text,
 latency_ms int,
 error_code text,
 error_message text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz,
 UNIQUE(workspace_id,id),
 FOREIGN KEY(workspace_id,provider_id) REFERENCES provider_connections(workspace_id,id),
 FOREIGN KEY(workspace_id,model_profile_id) REFERENCES model_profiles(workspace_id,id),
 FOREIGN KEY(workspace_id,job_id) REFERENCES jobs(workspace_id,id)
);
CREATE INDEX ai_invocations_recent ON ai_invocations(workspace_id,created_at DESC,id DESC);
CREATE INDEX ai_invocations_task ON ai_invocations(workspace_id,task,created_at DESC);

CREATE TABLE ai_budget_ledger (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 invocation_id uuid,
 task text NOT NULL DEFAULT '',
 day date NOT NULL,
 month_start date NOT NULL,
 reserved numeric(20,8) NOT NULL DEFAULT 0 CHECK(reserved >= 0),
 settled numeric(20,8) CHECK(settled >= 0),
 status text NOT NULL DEFAULT 'reserved' CHECK(status IN ('reserved','settled','released')),
 currency text NOT NULL DEFAULT 'USD',
 reason text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(),
 settled_at timestamptz,
 UNIQUE(workspace_id,id),
 FOREIGN KEY(workspace_id,invocation_id) REFERENCES ai_invocations(workspace_id,id)
);
CREATE UNIQUE INDEX ai_budget_ledger_invocation ON ai_budget_ledger(invocation_id)
 WHERE invocation_id IS NOT NULL;
CREATE INDEX ai_budget_ledger_day ON ai_budget_ledger(workspace_id,day);
CREATE INDEX ai_budget_ledger_month ON ai_budget_ledger(workspace_id,month_start);
