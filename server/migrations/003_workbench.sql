CREATE TABLE mad_tool_favorites (
    user_id uuid NOT NULL REFERENCES mad_users,
    tool_id text NOT NULL,
    PRIMARY KEY (user_id, tool_id)
);

CREATE TABLE mad_personal_tasks (
    user_id uuid NOT NULL REFERENCES mad_users,
    id uuid NOT NULL,
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 100),
    completed boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, id)
);

CREATE TABLE mad_calendar_events (
    user_id uuid NOT NULL REFERENCES mad_users,
    id uuid NOT NULL,
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 100),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL CHECK (ends_at > starts_at),
    task_id uuid,
    PRIMARY KEY (user_id, id),
    FOREIGN KEY (user_id, task_id) REFERENCES mad_personal_tasks (user_id, id)
);
CREATE INDEX mad_calendar_events_by_time ON mad_calendar_events (user_id, starts_at);
