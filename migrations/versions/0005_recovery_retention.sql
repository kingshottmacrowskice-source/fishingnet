-- M2 wrap-up: recoverable credential/risk-control state and bounded retention of
-- discovery candidate decisions.
--
-- Additive only. The six core tables keep their meaning; discovery run ledgers
-- are never removed, only their per-candidate detail rows expire. A connection
-- status is local bookkeeping, not a statement that the platform account was
-- revoked.
ALTER TABLE platform_connections DROP CONSTRAINT IF EXISTS platform_connections_status_check;
ALTER TABLE platform_connections ADD CONSTRAINT platform_connections_status_valid
 CHECK(status IN ('unconfigured','unverified','connected','expired','invalid','unavailable','risk_control'));
ALTER TABLE platform_connections ADD COLUMN cooldown_until timestamptz;
ALTER TABLE platform_connections ADD COLUMN last_success_at timestamptz;

CREATE INDEX discovery_runs_finished ON discovery_runs(workspace_id,finished_at)
 WHERE finished_at IS NOT NULL;
