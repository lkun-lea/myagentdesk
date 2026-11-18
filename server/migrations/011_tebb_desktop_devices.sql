CREATE TABLE mad_delivery_devices (
    id uuid PRIMARY KEY,
    actor uuid NOT NULL REFERENCES mad_users,
    label text NOT NULL,
    secret_digest bytea NOT NULL,
    credential_digest bytea,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX mad_delivery_devices_actor ON mad_delivery_devices(actor, created_at DESC);
ALTER TABLE mad_delivery_dispatches ADD COLUMN desktop boolean NOT NULL DEFAULT false;
