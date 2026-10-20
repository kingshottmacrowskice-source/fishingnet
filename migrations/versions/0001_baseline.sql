CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE workspace_settings (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL, timezone text NOT NULL DEFAULT 'Asia/Shanghai',
 daily_budget numeric(20,8) NOT NULL DEFAULT 0 CHECK(daily_budget>=0), monthly_budget numeric(20,8) NOT NULL DEFAULT 0 CHECK(monthly_budget>=0),
 data_policy text NOT NULL DEFAULT 'local_only' CHECK(data_policy IN ('local_only','approved_cloud')),
 created_at timestamptz NOT NULL DEFAULT now(), revision int NOT NULL DEFAULT 1
);
CREATE TABLE users (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 username text UNIQUE NOT NULL, password_hash text NOT NULL, display_name text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), revision int NOT NULL DEFAULT 1,
 UNIQUE(workspace_id,id)
);
CREATE UNIQUE INDEX single_administrator ON users ((true));
CREATE TABLE auth_sessions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, user_id uuid NOT NULL,
 token_hash text UNIQUE NOT NULL, csrf_hash text NOT NULL, device text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
 idle_expires_at timestamptz NOT NULL, absolute_expires_at timestamptz NOT NULL, revoked_at timestamptz,
 FOREIGN KEY(workspace_id,user_id) REFERENCES users(workspace_id,id)
);
CREATE TABLE sources (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 platform text NOT NULL, external_id text NOT NULL, source_kind text NOT NULL DEFAULT 'document',
 title text NOT NULL, author_name text, canonical_url text, published_at timestamptz,
 current_version_id uuid, availability text NOT NULL DEFAULT 'available', content_status text NOT NULL DEFAULT 'ready',
 lineage_group_id uuid, rights_policy jsonb NOT NULL, metadata jsonb NOT NULL DEFAULT '{"schema_version":"source.v1"}',
 reliability_validity text NOT NULL DEFAULT 'unknown', created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), revision int NOT NULL DEFAULT 1,
 UNIQUE(workspace_id,id), UNIQUE(workspace_id,platform,external_id)
);
CREATE TABLE source_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, source_id uuid NOT NULL,
 version_no int NOT NULL CHECK(version_no>0), raw_hash text NOT NULL, normalized_hash text NOT NULL,
 normalization_version text NOT NULL DEFAULT 'v1', extractor_version text NOT NULL DEFAULT 'manual.v1',
 content_origin text NOT NULL DEFAULT 'manual_text', language text NOT NULL DEFAULT 'zh', segments jsonb NOT NULL CHECK(jsonb_typeof(segments)='array'),
 coverage jsonb NOT NULL DEFAULT '{"status":"complete"}', created_at timestamptz NOT NULL DEFAULT now(),
 retention_expires_at timestamptz NOT NULL DEFAULT now()+interval '30 days', redacted_at timestamptz,
 FOREIGN KEY(workspace_id,source_id) REFERENCES sources(workspace_id,id), UNIQUE(workspace_id,id),
 UNIQUE(workspace_id,source_id,id), UNIQUE(workspace_id,source_id,version_no), UNIQUE(workspace_id,source_id,raw_hash,extractor_version)
);
ALTER TABLE sources ADD CONSTRAINT source_current_version FOREIGN KEY(workspace_id,id,current_version_id) REFERENCES source_versions(workspace_id,source_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE claims (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, origin_version_id uuid NOT NULL,
 statement text NOT NULL, claim_kind text NOT NULL CHECK(claim_kind IN ('factual_assertion','opinion','prediction','experience')),
 aspect text NOT NULL, qualifiers jsonb NOT NULL, polarity text NOT NULL CHECK(polarity IN ('affirm','deny','neutral')),
 extraction_confidence numeric, fact_check_status text NOT NULL DEFAULT 'unchecked', lifecycle_status text NOT NULL DEFAULT 'active',
 current_verification_id uuid, verification_validity text NOT NULL DEFAULT 'pending', importance text NOT NULL DEFAULT 'high',
 verifiability text NOT NULL DEFAULT 'verifiable', review_status text NOT NULL DEFAULT 'candidate',
 fingerprint text NOT NULL, provenance jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), revision int NOT NULL DEFAULT 1,
 FOREIGN KEY(workspace_id,origin_version_id) REFERENCES source_versions(workspace_id,id), UNIQUE(workspace_id,id),
 UNIQUE(workspace_id,origin_version_id,fingerprint)
);
CREATE TABLE nodes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 node_kind text NOT NULL CHECK(node_kind IN ('entity','topic')), canonical_name text NOT NULL,
 entity_type text, aliases jsonb NOT NULL DEFAULT '[]', identity_keys jsonb NOT NULL DEFAULT '{}',
 description text, status text NOT NULL DEFAULT 'provisional', metadata jsonb NOT NULL DEFAULT '{"schema_version":"node.v1"}',
 created_at timestamptz NOT NULL DEFAULT now(), revision int NOT NULL DEFAULT 1, UNIQUE(workspace_id,id)
);
CREATE TABLE updates (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 update_type text NOT NULL, title text NOT NULL, summary text NOT NULL, change_reason text NOT NULL,
 event_key text NOT NULL, state text NOT NULL DEFAULT 'draft', revision int NOT NULL DEFAULT 1,
 first_published_at timestamptz, last_changed_at timestamptz NOT NULL DEFAULT now(),
 input_fingerprint text NOT NULL, structured_body jsonb NOT NULL, revision_history jsonb NOT NULL DEFAULT '[]',
 provenance jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(workspace_id,id), UNIQUE(workspace_id,event_key)
);
CREATE TABLE relations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 from_type text NOT NULL, from_id uuid NOT NULL, relation_type text NOT NULL, to_type text NOT NULL, to_id uuid NOT NULL,
 payload jsonb NOT NULL, status text NOT NULL DEFAULT 'proposed', asserted_by text NOT NULL, confidence numeric,
 dedup_key text NOT NULL, provenance jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,id), UNIQUE(workspace_id,dedup_key)
);
CREATE INDEX relations_forward ON relations(workspace_id,from_type,from_id,relation_type);
CREATE INDEX relations_reverse ON relations(workspace_id,to_type,to_id,relation_type);
CREATE INDEX sources_search ON sources USING gin(title gin_trgm_ops);
CREATE INDEX nodes_search ON nodes USING gin(canonical_name gin_trgm_ops);
CREATE INDEX updates_feed ON updates(workspace_id,first_published_at DESC,id DESC) WHERE state='published';
CREATE TABLE verification_records (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, record_kind text NOT NULL DEFAULT 'claim_fact_check',
 claim_id uuid, source_id uuid, version_no int NOT NULL, supersedes_id uuid,
 schema_version text NOT NULL DEFAULT 'verification.v1', policy_version text NOT NULL,
 input_fingerprint text NOT NULL, target_revision int NOT NULL, trigger text NOT NULL, job_id uuid,
 checker_type text NOT NULL, retrieval_manifest jsonb NOT NULL, evidence_snapshot jsonb NOT NULL,
 applicability jsonb NOT NULL, valid_until timestamptz, assessment jsonb NOT NULL, model_runs jsonb NOT NULL DEFAULT '[]',
 review_status text NOT NULL DEFAULT 'pending', reviewer_id uuid, review_reason text, reviewed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((record_kind='claim_fact_check' AND claim_id IS NOT NULL AND source_id IS NULL) OR (record_kind='source_reliability' AND source_id IS NOT NULL AND claim_id IS NULL)),
 FOREIGN KEY(workspace_id,claim_id) REFERENCES claims(workspace_id,id), FOREIGN KEY(workspace_id,source_id) REFERENCES sources(workspace_id,id),
 FOREIGN KEY(workspace_id,reviewer_id) REFERENCES users(workspace_id,id), UNIQUE(workspace_id,id), UNIQUE(workspace_id,claim_id,id),
 UNIQUE(workspace_id,claim_id,version_no), UNIQUE(workspace_id,source_id,version_no),
 FOREIGN KEY(workspace_id,supersedes_id) REFERENCES verification_records(workspace_id,id)
);
ALTER TABLE claims ADD CONSTRAINT current_verification FOREIGN KEY(workspace_id,id,current_verification_id) REFERENCES verification_records(workspace_id,claim_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 kind text NOT NULL, input jsonb NOT NULL, config_snapshot jsonb NOT NULL, idempotency_key text NOT NULL,
 state text NOT NULL DEFAULT 'queued', stage text NOT NULL DEFAULT 'queued', attempts int NOT NULL DEFAULT 0,
 fence int NOT NULL DEFAULT 0, lease_until timestamptz, error_code text, result jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,id), UNIQUE(workspace_id,idempotency_key)
);
CREATE TABLE job_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, job_id uuid NOT NULL, fence int NOT NULL,
 state text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), FOREIGN KEY(workspace_id,job_id) REFERENCES jobs(workspace_id,id)
);
CREATE TABLE request_idempotency (
 workspace_id uuid NOT NULL REFERENCES workspace_settings(id), key text NOT NULL, body_hash text NOT NULL,
 response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(workspace_id,key)
);
CREATE TABLE user_item_states (
 workspace_id uuid NOT NULL, user_id uuid NOT NULL, update_id uuid NOT NULL,
 read_revision int NOT NULL DEFAULT 0, bookmarked boolean NOT NULL DEFAULT false,
 FOREIGN KEY(workspace_id,user_id) REFERENCES users(workspace_id,id), FOREIGN KEY(workspace_id,update_id) REFERENCES updates(workspace_id,id), PRIMARY KEY(user_id,update_id)
);
CREATE TABLE subscriptions (
 workspace_id uuid NOT NULL, user_id uuid NOT NULL, node_id uuid NOT NULL,
 FOREIGN KEY(workspace_id,user_id) REFERENCES users(workspace_id,id), FOREIGN KEY(workspace_id,node_id) REFERENCES nodes(workspace_id,id), PRIMARY KEY(user_id,node_id)
);
CREATE TABLE audit_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 action text NOT NULL, object_id uuid, details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE feedback (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
 object_id uuid NOT NULL, category text NOT NULL, comment text NOT NULL, status text NOT NULL DEFAULT 'pending', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION immutable_row() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable record'; END $$;
CREATE TRIGGER immutable_versions BEFORE UPDATE OR DELETE ON source_versions FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_checks BEFORE UPDATE OR DELETE ON verification_records FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION immutable_row();

CREATE FUNCTION relation_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a text; b text; exists_a boolean; exists_b boolean; seg jsonb; q text; k1 text; k2 text;
BEGIN
 a := CASE NEW.from_type WHEN 'claim' THEN 'claims' WHEN 'node' THEN 'nodes' WHEN 'update' THEN 'updates' WHEN 'source_version' THEN 'source_versions' ELSE NULL END;
 b := CASE NEW.to_type WHEN 'claim' THEN 'claims' WHEN 'node' THEN 'nodes' WHEN 'update' THEN 'updates' WHEN 'source_version' THEN 'source_versions' ELSE NULL END;
 IF a IS NULL OR b IS NULL THEN RAISE EXCEPTION 'invalid endpoint type'; END IF;
 EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I WHERE workspace_id=$1 AND id=$2)',a) INTO exists_a USING NEW.workspace_id,NEW.from_id;
 EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I WHERE workspace_id=$1 AND id=$2)',b) INTO exists_b USING NEW.workspace_id,NEW.to_id;
 IF NOT exists_a OR NOT exists_b THEN RAISE EXCEPTION 'missing or cross-workspace endpoint'; END IF;
 IF NOT (
  (NEW.from_type='claim' AND NEW.to_type='source_version' AND NEW.relation_type IN ('evidenced_by','fact_supported_by','fact_refuted_by','fact_context_by')) OR
  (NEW.from_type IN ('claim','update') AND NEW.to_type='node' AND NEW.relation_type='about') OR
  (NEW.from_type='update' AND NEW.to_type='claim' AND NEW.relation_type='includes') OR
  (NEW.from_type='node' AND NEW.to_type='node' AND NEW.relation_type IN ('member_of','subtopic_of')) OR
  (NEW.from_type='claim' AND NEW.to_type='claim' AND NEW.relation_type IN ('equivalent_to','supports','contradicts','qualifies','supersedes'))
 ) THEN RAISE EXCEPTION 'invalid relation matrix'; END IF;
 IF NEW.relation_type IN ('member_of','subtopic_of') THEN
  SELECT node_kind INTO k1 FROM nodes WHERE id=NEW.from_id;
  SELECT node_kind INTO k2 FROM nodes WHERE id=NEW.to_id;
  IF k2 <> 'topic' OR (NEW.relation_type='member_of' AND k1 <> 'entity') OR (NEW.relation_type='subtopic_of' AND k1 <> 'topic') THEN RAISE EXCEPTION 'invalid node subtype'; END IF;
 END IF;
 IF NEW.relation_type IN ('subtopic_of','supersedes') AND NEW.status='accepted' THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.workspace_id::text,0));
  IF NEW.from_id=NEW.to_id OR EXISTS(WITH RECURSIVE reach(id) AS (
   SELECT NEW.to_id UNION SELECT r.to_id FROM relations r JOIN reach x ON r.from_id=x.id WHERE r.workspace_id=NEW.workspace_id AND r.relation_type=NEW.relation_type AND r.status='accepted' AND r.id<>NEW.id
  ) SELECT 1 FROM reach WHERE id=NEW.from_id) THEN RAISE EXCEPTION 'relation cycle'; END IF;
 END IF;
 IF NEW.relation_type='evidenced_by' AND NEW.status='accepted' THEN
  SELECT s INTO seg FROM source_versions v, jsonb_array_elements(v.segments) s WHERE v.id=NEW.to_id AND s->>'segment_id'=NEW.payload->>'segment_id';
  IF seg IS NULL OR NOT (NEW.payload ?& ARRAY['char_start','char_end','quote','quote_hash']) THEN RAISE EXCEPTION 'invalid anchor'; END IF;
  IF (NEW.payload->>'char_start')::int<0 OR (NEW.payload->>'char_end')::int<=(NEW.payload->>'char_start')::int OR (NEW.payload->>'char_end')::int>char_length(seg->>'text') THEN RAISE EXCEPTION 'invalid offset'; END IF;
  q := substring(seg->>'text' FROM (NEW.payload->>'char_start')::int+1 FOR (NEW.payload->>'char_end')::int-(NEW.payload->>'char_start')::int);
  IF q<>NEW.payload->>'quote' OR encode(sha256(convert_to(q,'UTF8')),'hex')<>NEW.payload->>'quote_hash' THEN RAISE EXCEPTION 'invalid quote'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER relation_check AFTER INSERT OR UPDATE ON relations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION relation_integrity();
CREATE FUNCTION validated_claim_evidence() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.review_status='validated' AND NOT EXISTS(SELECT 1 FROM relations WHERE workspace_id=NEW.workspace_id AND from_id=NEW.id AND relation_type='evidenced_by' AND to_id=NEW.origin_version_id AND status='accepted') THEN RAISE EXCEPTION 'validated claim requires origin evidence'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER claim_evidence_check AFTER INSERT OR UPDATE ON claims DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validated_claim_evidence();
CREATE FUNCTION prevent_endpoint_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM relations WHERE workspace_id=OLD.workspace_id AND (from_id=OLD.id OR to_id=OLD.id)) THEN RAISE EXCEPTION 'endpoint has relations'; END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER claims_delete BEFORE DELETE ON claims FOR EACH ROW EXECUTE FUNCTION prevent_endpoint_delete();
CREATE TRIGGER nodes_delete BEFORE DELETE ON nodes FOR EACH ROW EXECUTE FUNCTION prevent_endpoint_delete();
CREATE TRIGGER updates_delete BEFORE DELETE ON updates FOR EACH ROW EXECUTE FUNCTION prevent_endpoint_delete();
