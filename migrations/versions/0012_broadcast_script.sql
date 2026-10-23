CREATE TABLE broadcast_script_configs (
    workspace_id uuid PRIMARY KEY REFERENCES workspace_settings(id),
    opening text NOT NULL DEFAULT '',
    anchor_persona text NOT NULL DEFAULT '',
    expressions jsonb NOT NULL DEFAULT '["微笑","认真","点头","严肃","惊讶","放松","好奇","肯定"]',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE broadcast_scripts (
    workspace_id uuid NOT NULL REFERENCES workspace_settings(id),
    day date NOT NULL,
    opening text NOT NULL DEFAULT '',
    segments jsonb NOT NULL DEFAULT '[]',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(workspace_id, day)
);
