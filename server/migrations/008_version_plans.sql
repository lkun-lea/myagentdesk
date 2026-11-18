CREATE TABLE mad_version_plans (
    project_id uuid NOT NULL REFERENCES mad_projects,
    id uuid NOT NULL,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE mad_version_plan_revisions (
    project_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    name text NOT NULL,
    owner_id uuid NOT NULL REFERENCES mad_users,
    target_date date,
    status text NOT NULL CHECK (status IN ('draft', 'committed', 'cancelled')),
    requirement_ids uuid[] NOT NULL,
    milestones jsonb NOT NULL,
    required_checks jsonb NOT NULL,
    reason text NOT NULL,
    actor_id uuid NOT NULL REFERENCES mad_users,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, plan_id, revision),
    FOREIGN KEY (project_id, plan_id) REFERENCES mad_version_plans(project_id, id)
);
