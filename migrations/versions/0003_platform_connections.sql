-- M2 Phase 1: platform connections (credential + capability state) and source freshness.
CREATE TABLE platform_connections (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 platform text NOT NULL, alias text NOT NULL DEFAULT '',
 credential_kind text NOT NULL DEFAULT 'session_cookie',
 credential_sealed text,
 status text NOT NULL DEFAULT 'unconfigured',
 capabilities jsonb NOT NULL DEFAULT '{"schema_version":"platform-capability.v1"}',
 last_checked_at timestamptz, last_error_code text, last_check_detail text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 revision int NOT NULL DEFAULT 1,
 UNIQUE(workspace_id,platform), UNIQUE(workspace_id,id),
 CHECK(status IN ('unconfigured','unverified','connected','expired','invalid','unavailable')),
 CHECK(credential_kind IN ('session_cookie'))
);
ALTER TABLE sources ADD COLUMN last_checked_at timestamptz;
CREATE INDEX sources_platform ON sources(workspace_id,platform,last_checked_at DESC NULLS LAST);
