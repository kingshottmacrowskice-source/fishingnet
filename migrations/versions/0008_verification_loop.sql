-- M4 Phase 1: fact evidence materials, versioned verification records and human review.
-- Six core tables keep their meaning: fact evidence is a relation, assessment is a
-- versioned verification record, and the publication gate stays server-side.

-- A claim -> source_version fact relation must carry the versioned payload required by
-- requirement 6.5 and an anchor that is recomputed from the stored immutable version.
CREATE FUNCTION fact_evidence_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE seg jsonb; anchor jsonb; q text;
BEGIN
 IF NEW.relation_type NOT IN ('fact_supported_by','fact_refuted_by','fact_context_by') THEN RETURN NEW; END IF;
 IF NEW.from_type<>'claim' OR NEW.to_type<>'source_version' THEN RAISE EXCEPTION 'invalid fact evidence endpoints'; END IF;
 IF coalesce(NEW.payload->>'schema_version','') <> 'fact_evidence.v1' THEN RAISE EXCEPTION 'unsupported fact evidence schema'; END IF;
 IF NOT (NEW.payload ?& ARRAY['anchor','proposition_scope','directness','origin_group_id','independence_status','applicability','artifact_integrity','method_transparency','limitations']) THEN
  RAISE EXCEPTION 'incomplete fact evidence payload';
 END IF;
 IF coalesce(NEW.payload->>'directness','') NOT IN ('direct','indirect') THEN RAISE EXCEPTION 'invalid directness'; END IF;
 IF coalesce(NEW.payload->>'independence_status','') NOT IN ('confirmed','shared_origin','unknown') THEN RAISE EXCEPTION 'invalid independence status'; END IF;
 IF coalesce(NEW.payload->>'artifact_integrity','') NOT IN ('verified','unverified','suspected_manipulation') THEN RAISE EXCEPTION 'invalid artifact integrity'; END IF;
 IF jsonb_typeof(NEW.payload->'applicability')<>'object' OR jsonb_typeof(NEW.payload->'method_transparency')<>'object'
    OR jsonb_typeof(NEW.payload->'limitations')<>'array' THEN RAISE EXCEPTION 'invalid fact evidence payload shape'; END IF;
 IF coalesce(NEW.payload->>'proposition_scope','')='' OR coalesce(NEW.payload->>'origin_group_id','')='' THEN
  RAISE EXCEPTION 'missing scope or origin group';
 END IF;
 anchor := NEW.payload->'anchor';
 IF anchor IS NULL OR jsonb_typeof(anchor)<>'object' OR NOT (anchor ?& ARRAY['segment_id','char_start','char_end','quote','quote_hash']) THEN
  RAISE EXCEPTION 'invalid anchor';
 END IF;
 SELECT s INTO seg FROM source_versions v, jsonb_array_elements(v.segments) s
  WHERE v.id=NEW.to_id AND s->>'segment_id'=anchor->>'segment_id';
 IF seg IS NULL THEN RAISE EXCEPTION 'invalid anchor segment'; END IF;
 IF (anchor->>'char_start')::int<0 OR (anchor->>'char_end')::int<=(anchor->>'char_start')::int
    OR (anchor->>'char_end')::int>char_length(seg->>'text') THEN RAISE EXCEPTION 'invalid offset'; END IF;
 q := substring(seg->>'text' FROM (anchor->>'char_start')::int+1 FOR (anchor->>'char_end')::int-(anchor->>'char_start')::int);
 IF q<>anchor->>'quote' OR encode(sha256(convert_to(q,'UTF8')),'hex')<>anchor->>'quote_hash' THEN
  RAISE EXCEPTION 'invalid quote';
 END IF;
 -- A fact material becomes usable only together with the accepted assessment that used it.
 IF NEW.status='accepted' AND coalesce(NEW.payload->>'assessment_record_id','')='' THEN
  RAISE EXCEPTION 'accepted fact evidence requires an assessment record';
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER fact_evidence_check AFTER INSERT OR UPDATE ON relations
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fact_evidence_integrity();

ALTER TABLE relations ADD CONSTRAINT fact_evidence_status
 CHECK(relation_type NOT IN ('fact_supported_by','fact_refuted_by','fact_context_by')
       OR status IN ('proposed','accepted','rejected'));

ALTER TABLE verification_records ADD CONSTRAINT verification_review_status
 CHECK(review_status IN ('pending','accepted','rejected'));
ALTER TABLE verification_records ADD CONSTRAINT verification_checker
 CHECK(checker_type IN ('rule','model','human'));
ALTER TABLE verification_records ADD CONSTRAINT verification_trigger
 CHECK(trigger IN ('new_claim','manual','evidence_added','evidence_retired','recheck_due','policy_upgrade'));

CREATE INDEX verification_records_claim_idx ON verification_records(workspace_id, claim_id, created_at DESC);
CREATE INDEX verification_records_pending_idx ON verification_records(workspace_id, review_status)
 WHERE review_status='pending';
CREATE INDEX relations_fact_evidence_idx ON relations(workspace_id, from_id, relation_type)
 WHERE relation_type IN ('fact_supported_by','fact_refuted_by','fact_context_by');
CREATE INDEX claims_fact_status_idx ON claims(workspace_id, fact_check_status);
