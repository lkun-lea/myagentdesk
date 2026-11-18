CREATE TABLE mad_delivery_dispatches (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES mad_projects,
    group_id uuid NOT NULL REFERENCES mad_requirement_groups,
    group_revision integer NOT NULL,
    actor uuid NOT NULL REFERENCES mad_users,
    stage text NOT NULL CHECK (stage IN ('development', 'testing')),
    secret_digest bytea NOT NULL,
    input jsonb NOT NULL,
    device uuid,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled','needs_review','historical')),
    receipt jsonb,
    artifact bytea,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mad_delivery_dispatch_group ON mad_delivery_dispatches(group_id, group_revision, created_at DESC);

CREATE TABLE mad_delivery_publishers (
    project_id uuid NOT NULL,
    user_id uuid NOT NULL,
    PRIMARY KEY (project_id, user_id),
    FOREIGN KEY (project_id, user_id) REFERENCES mad_memberships ON DELETE CASCADE
);

CREATE TABLE mad_delivery_builds (
    candidate_id uuid NOT NULL REFERENCES mad_delivery_dispatches,
    verification_id uuid NOT NULL REFERENCES mad_delivery_dispatches,
    project_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    plan_revision integer NOT NULL,
    quality_id uuid NOT NULL REFERENCES mad_users,
    ci jsonb NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, plan_id, plan_revision, candidate_id),
    FOREIGN KEY (project_id, plan_id, plan_revision) REFERENCES mad_version_plan_revisions
);

CREATE TABLE mad_delivery_releases (
    project_id uuid NOT NULL,
    plan_id uuid NOT NULL,
    plan_revision integer NOT NULL,
    candidate_id uuid NOT NULL,
    publisher_id uuid NOT NULL REFERENCES mad_users,
    payload jsonb NOT NULL,
    published_at timestamptz NOT NULL,
    PRIMARY KEY (project_id, plan_id, plan_revision),
    FOREIGN KEY (project_id, plan_id, plan_revision, candidate_id) REFERENCES mad_delivery_builds
);
