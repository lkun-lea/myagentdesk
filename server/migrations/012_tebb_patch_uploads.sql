CREATE TABLE mad_delivery_uploads (
    dispatch uuid PRIMARY KEY REFERENCES mad_delivery_dispatches ON DELETE CASCADE,
    digest text NOT NULL,
    size integer NOT NULL CHECK (size BETWEEN 1 AND 524288),
    chunk_bytes integer NOT NULL CHECK (chunk_bytes BETWEEN 128 AND 32768),
    chunks jsonb NOT NULL
);

CREATE TABLE mad_delivery_chunks (
    dispatch uuid NOT NULL REFERENCES mad_delivery_uploads ON DELETE CASCADE,
    position integer NOT NULL CHECK (position BETWEEN 0 AND 255),
    payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 32768),
    PRIMARY KEY (dispatch, position)
);
