CREATE TABLE mad_requirement_groups (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES mad_projects,
    requirement_id uuid NOT NULL,
    requirement_revision integer NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    stage text NOT NULL DEFAULT 'product',
    state text NOT NULL DEFAULT 'active',
    auto_run boolean NOT NULL DEFAULT true,
    source text NOT NULL,
    product_id uuid NOT NULL REFERENCES mad_users,
    development_id uuid NOT NULL REFERENCES mad_users,
    testing_id uuid NOT NULL REFERENCES mad_users,
    quality_id uuid NOT NULL REFERENCES mad_users,
    last_handoff uuid REFERENCES mad_handoffs (run_id),
    UNIQUE (project_id,requirement_id),
    CHECK (development_id <> testing_id),
    FOREIGN KEY (project_id,requirement_id,requirement_revision)
        REFERENCES mad_requirements (project_id,id,revision)
);
ALTER TABLE mad_agent_runs ADD COLUMN group_id uuid REFERENCES mad_requirement_groups;
ALTER TABLE mad_agent_runs ADD COLUMN group_revision integer;
ALTER TABLE mad_agent_runs ADD COLUMN candidate_run uuid REFERENCES mad_handoffs (run_id);
CREATE UNIQUE INDEX mad_one_group_stage_run ON mad_agent_runs (group_id,group_revision) WHERE group_id IS NOT NULL;
CREATE TABLE mad_run_contributors (
    run_id uuid NOT NULL REFERENCES mad_agent_runs,
    user_id uuid NOT NULL REFERENCES mad_users,
    role text NOT NULL,
    PRIMARY KEY (run_id,user_id)
);
INSERT INTO mad_run_contributors SELECT id,user_id,role FROM mad_agent_runs;
CREATE TABLE mad_group_messages (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id uuid NOT NULL UNIQUE,
    group_id uuid NOT NULL REFERENCES mad_requirement_groups,
    author uuid NOT NULL REFERENCES mad_users,
    kind text NOT NULL,
    body text NOT NULL,
    data jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mad_group_message_cursor ON mad_group_messages (group_id,sequence);
CREATE TABLE mad_group_decisions (
    group_id uuid NOT NULL REFERENCES mad_requirement_groups,
    revision integer NOT NULL,
    actor uuid NOT NULL REFERENCES mad_users,
    action text NOT NULL,
    note text NOT NULL,
    result jsonb NOT NULL,
    PRIMARY KEY (group_id,revision)
);
CREATE TABLE mad_run_decisions (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES mad_agent_runs,
    generation integer NOT NULL,
    owner_id uuid NOT NULL REFERENCES mad_users,
    question text NOT NULL,
    options jsonb NOT NULL,
    evidence jsonb NOT NULL,
    checkpoint integer NOT NULL,
    state text NOT NULL DEFAULT 'pending',
    revision integer NOT NULL DEFAULT 1,
    answer text,
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
CREATE UNIQUE INDEX mad_one_pending_decision ON mad_run_decisions (run_id,generation) WHERE state='pending';
