CREATE TABLE mad_agents (
    user_id uuid PRIMARY KEY REFERENCES mad_users,
    id uuid NOT NULL UNIQUE,
    revision integer NOT NULL DEFAULT 1,
    model text NOT NULL,
    skills jsonb NOT NULL DEFAULT '[]'
);
CREATE TABLE mad_project_roles (
    project_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('product', 'development', 'testing')),
    PRIMARY KEY (project_id, user_id),
    FOREIGN KEY (project_id, user_id) REFERENCES mad_memberships ON DELETE CASCADE
);
CREATE TABLE mad_context_documents (
    project_id uuid NOT NULL REFERENCES mad_projects,
    id uuid NOT NULL,
    revision integer NOT NULL,
    title text NOT NULL,
    source text NOT NULL,
    body text NOT NULL,
    author uuid NOT NULL REFERENCES mad_users,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, id, revision)
);
CREATE TABLE mad_requirements (
    project_id uuid NOT NULL REFERENCES mad_projects,
    id uuid NOT NULL,
    revision integer NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    criteria jsonb NOT NULL,
    author uuid NOT NULL REFERENCES mad_users,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, id, revision)
);
CREATE TABLE mad_agent_runs (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    requirement_revision integer NOT NULL,
    user_id uuid NOT NULL REFERENCES mad_users,
    agent_id uuid NOT NULL REFERENCES mad_agents (id),
    role text NOT NULL,
    config jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    checkpoint integer NOT NULL DEFAULT 0,
    generation integer NOT NULL DEFAULT 0,
    confirmed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, requirement_id, requirement_revision)
        REFERENCES mad_requirements (project_id, id, revision)
);
CREATE TABLE mad_run_receipts (
    run_id uuid NOT NULL REFERENCES mad_agent_runs,
    call_id text NOT NULL,
    generation integer NOT NULL,
    tool text NOT NULL,
    arguments jsonb NOT NULL,
    result jsonb NOT NULL,
    PRIMARY KEY (run_id, generation, call_id)
);
CREATE TABLE mad_run_references (
    run_id uuid NOT NULL REFERENCES mad_agent_runs,
    document_id uuid NOT NULL,
    revision integer NOT NULL,
    start_offset integer NOT NULL,
    end_offset integer NOT NULL,
    active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (run_id, document_id, revision, start_offset, end_offset)
);
CREATE TABLE mad_run_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES mad_agent_runs,
    kind text NOT NULL,
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE mad_handoffs (
    run_id uuid PRIMARY KEY REFERENCES mad_agent_runs,
    project_id uuid NOT NULL,
    requirement_id uuid NOT NULL,
    requirement_revision integer NOT NULL,
    author uuid NOT NULL REFERENCES mad_users,
    role text NOT NULL,
    summary text NOT NULL,
    evidence jsonb NOT NULL,
    context_references jsonb NOT NULL,
    verdict text NOT NULL,
    candidate_run uuid REFERENCES mad_handoffs (run_id),
    created_at timestamptz NOT NULL DEFAULT now()
);
