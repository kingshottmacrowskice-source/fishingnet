CREATE TABLE daily_digests (
    workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
    day date NOT NULL,
    summary text NOT NULL DEFAULT '',
    markdown text NOT NULL DEFAULT '',
    video_count int NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(workspace_id, day)
);
