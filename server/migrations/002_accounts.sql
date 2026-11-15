CREATE TABLE mad_users (
    id uuid PRIMARY KEY,
    email text NOT NULL
);
ALTER TABLE mad_workspaces ADD COLUMN owner_id uuid REFERENCES mad_users;

CREATE TABLE mad_memberships (
    project_id uuid NOT NULL REFERENCES mad_projects,
    user_id uuid NOT NULL REFERENCES mad_users,
    role text NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    PRIMARY KEY (project_id, user_id)
);
CREATE UNIQUE INDEX mad_project_owner ON mad_memberships (project_id) WHERE role = 'owner';
CREATE INDEX mad_memberships_user ON mad_memberships (user_id, project_id);

CREATE TABLE mad_sessions (
    digest text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES mad_users,
    upstream bytea NOT NULL,
    access_until timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE mad_auth_operations (
    flow text NOT NULL,
    id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('send', 'verify')),
    email text NOT NULL,
    challenge_id uuid,
    outcome text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    session_digest text REFERENCES mad_sessions ON DELETE SET NULL,
    PRIMARY KEY (flow, id)
);
CREATE INDEX mad_auth_operations_email ON mad_auth_operations (email, started_at);

CREATE TABLE mad_invites (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES mad_projects,
    email text NOT NULL,
    creator uuid NOT NULL REFERENCES mad_users,
    expires_at timestamptz NOT NULL,
    accepted_by uuid REFERENCES mad_users,
    revoked_at timestamptz
);
CREATE INDEX mad_invites_project ON mad_invites (project_id);

CREATE TABLE mad_project_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES mad_projects,
    actor uuid NOT NULL REFERENCES mad_users,
    target text NOT NULL,
    action text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mad_project_events_project ON mad_project_events (project_id, id);
