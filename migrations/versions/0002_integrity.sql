CREATE FUNCTION current_verification_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r verification_records;
BEGIN
 IF NEW.current_verification_id IS NOT NULL THEN
  SELECT * INTO r FROM verification_records WHERE id=NEW.current_verification_id AND workspace_id=NEW.workspace_id AND claim_id=NEW.id;
  IF r.id IS NULL OR r.review_status<>'accepted' THEN RAISE EXCEPTION 'current verification must be accepted for this claim'; END IF;
  IF NEW.verification_validity='current' AND (r.target_revision<>NEW.revision OR r.input_fingerprint<>NEW.fingerprint) THEN RAISE EXCEPTION 'stale verification pointer'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER verification_pointer_check AFTER INSERT OR UPDATE ON claims DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION current_verification_integrity();
CREATE FUNCTION protect_claim_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.relation_type='evidenced_by' AND EXISTS(SELECT 1 FROM claims c WHERE c.id=OLD.from_id AND c.review_status='validated' AND NOT EXISTS(
  SELECT 1 FROM relations r WHERE r.from_id=c.id AND r.to_id=c.origin_version_id AND r.relation_type='evidenced_by' AND r.status='accepted')) THEN RAISE EXCEPTION 'validated claim cannot lose last origin anchor'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER evidence_retirement_check AFTER UPDATE OR DELETE ON relations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION protect_claim_evidence();
ALTER TABLE jobs ADD CONSTRAINT job_state CHECK(state IN ('queued','running','succeeded','retry_wait','blocked_budget','blocked_credentials','needs_review','failed','cancel_requested','cancelled'));
ALTER TABLE claims ADD CONSTRAINT fact_status CHECK(fact_check_status IN ('unchecked','insufficient','corroborated','conflicted','refuted'));
ALTER TABLE claims ADD CONSTRAINT validity CHECK(verification_validity IN ('current','stale','pending'));
