CREATE TABLE mad_workspaces (
    id uuid PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE mad_projects (
    id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES mad_workspaces,
    name text NOT NULL,
    UNIQUE (workspace_id, id)
);
