CREATE TABLE mad_github_connections (
    project_id uuid PRIMARY KEY REFERENCES mad_projects,
    repository_id bigint NOT NULL CHECK (repository_id > 0),
    revision integer NOT NULL DEFAULT 1,
    enabled boolean NOT NULL DEFAULT true
);

CREATE TABLE mad_github_issue_links (
    project_id uuid NOT NULL REFERENCES mad_projects,
    repository_id bigint NOT NULL CHECK (repository_id > 0),
    issue_number integer NOT NULL CHECK (issue_number > 0),
    requirement_id uuid NOT NULL,
    linked_revision integer NOT NULL,
    linked_by uuid NOT NULL REFERENCES mad_users,
    linked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, repository_id, issue_number),
    FOREIGN KEY (project_id, requirement_id, linked_revision) REFERENCES mad_requirements(project_id, id, revision)
);

CREATE INDEX mad_github_requirement_links ON mad_github_issue_links (project_id, requirement_id, repository_id);
