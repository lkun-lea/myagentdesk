ALTER TABLE mad_requirements ADD COLUMN specification jsonb;
ALTER TABLE mad_handoffs ADD COLUMN case_results jsonb NOT NULL DEFAULT '[]';

CREATE INDEX mad_testing_handoff_history ON mad_handoffs (project_id, requirement_id, requirement_revision, created_at DESC)
    WHERE role = 'testing';
