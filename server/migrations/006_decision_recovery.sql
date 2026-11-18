ALTER TABLE mad_agent_runs ADD COLUMN execution_revision integer NOT NULL DEFAULT 1;
ALTER TABLE mad_run_receipts ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE mad_run_decisions ADD COLUMN receipt jsonb;
ALTER TABLE mad_run_decisions ADD COLUMN context_references jsonb NOT NULL DEFAULT '[]';
ALTER TABLE mad_run_decisions ADD COLUMN due_at timestamptz NOT NULL DEFAULT (now() + interval '24 hours');
CREATE TABLE mad_resume_jobs (
    run_id uuid PRIMARY KEY REFERENCES mad_agent_runs,
    user_id uuid NOT NULL REFERENCES mad_users,
    execution_revision integer NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    state text NOT NULL DEFAULT 'queued',
    reason text,
    attempts integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mad_resume_ready ON mad_resume_jobs(state,updated_at);
CREATE TABLE mad_project_resources (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES mad_projects,
    name text NOT NULL,
    created_by uuid NOT NULL REFERENCES mad_users,
    UNIQUE(project_id,name)
);
CREATE TABLE mad_resource_grants (
    resource_id uuid NOT NULL REFERENCES mad_project_resources,
    user_id uuid NOT NULL REFERENCES mad_users,
    access text NOT NULL CHECK(access IN ('read','write','none')),
    revision integer NOT NULL DEFAULT 1,
    PRIMARY KEY(resource_id,user_id)
);
CREATE TABLE mad_group_resources (
    group_id uuid NOT NULL REFERENCES mad_requirement_groups,
    resource_id uuid NOT NULL REFERENCES mad_project_resources,
    PRIMARY KEY(group_id,resource_id)
);
CREATE TABLE mad_run_resources (
    run_id uuid NOT NULL REFERENCES mad_agent_runs,
    resource_id uuid NOT NULL REFERENCES mad_project_resources,
    PRIMARY KEY(run_id,resource_id)
);
CREATE TABLE mad_artifacts (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES mad_agent_runs,
    generation integer NOT NULL,
    resource_id uuid NOT NULL REFERENCES mad_project_resources,
    operation_key text NOT NULL,
    digest text NOT NULL,
    bytes integer NOT NULL,
    UNIQUE(run_id,generation,resource_id,operation_key)
);
CREATE TABLE mad_decision_escalations (
    decision_id uuid NOT NULL REFERENCES mad_run_decisions,
    revision integer NOT NULL,
    actor uuid NOT NULL REFERENCES mad_users,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(decision_id,revision)
);
