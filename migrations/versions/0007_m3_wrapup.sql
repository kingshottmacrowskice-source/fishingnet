-- M3 wrap-up: expression-support review records and reviewable model entity links.
--
-- Additive only. ``claim_expression_reviews`` is an append-only audit of a second
-- model judging whether an already anchored quote supports the claim *as expressed*.
-- It is deliberately NOT a fact-check record: it never sets ``fact_check_status``
-- and never grants fact confirmation. Entity disambiguation produces ordinary
-- ``relations`` rows with ``status='proposed'``; a user must accept them before they
-- are used for entity views, and no model suggestion can create a node.
CREATE TABLE claim_expression_reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL,
 claim_id uuid NOT NULL,
 job_id uuid,
 invocation_id uuid,
 record_kind text NOT NULL DEFAULT 'expression_support'
   CHECK(record_kind IN ('expression_support')),
 schema_version text NOT NULL DEFAULT 'expression_review.v1',
 policy_version text NOT NULL,
 verdict text NOT NULL CHECK(verdict IN ('supports','partially_supports','unsupported','unclear')),
 rationale text NOT NULL DEFAULT '',
 limitations jsonb NOT NULL DEFAULT '[]',
 quote_count int NOT NULL DEFAULT 0 CHECK(quote_count >= 0),
 model_label text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,id),
 FOREIGN KEY(workspace_id,claim_id) REFERENCES claims(workspace_id,id),
 FOREIGN KEY(workspace_id,job_id) REFERENCES jobs(workspace_id,id),
 FOREIGN KEY(workspace_id,invocation_id) REFERENCES ai_invocations(workspace_id,id),
 CHECK(jsonb_typeof(limitations)='array')
);
CREATE INDEX claim_expression_reviews_claim
 ON claim_expression_reviews(workspace_id,claim_id,created_at DESC);
CREATE INDEX claim_expression_reviews_invocation
 ON claim_expression_reviews(workspace_id,invocation_id);
CREATE TRIGGER immutable_expression_reviews BEFORE UPDATE OR DELETE ON claim_expression_reviews
 FOR EACH ROW EXECUTE FUNCTION immutable_row();

-- Proposed model links are reviewed from the claim drawer; this partial index keeps
-- the "what is waiting for a human" query cheap without changing existing indexes.
CREATE INDEX relations_proposed_review
 ON relations(workspace_id,from_type,created_at)
 WHERE status='proposed' AND asserted_by='model';
