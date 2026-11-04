CREATE TABLE IF NOT EXISTS "EvolveAnalyticsMeta" (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    started_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO "EvolveAnalyticsMeta"(singleton) VALUES (true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS "EvolveFilePolicy" (
    file_id text PRIMARY KEY REFERENCES "File"(id) ON DELETE CASCADE,
    require_email boolean NOT NULL DEFAULT false,
    revision integer NOT NULL DEFAULT 1,
    uploader_name text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS "EvolveViewGrant" (
    visitor_id text NOT NULL,
    file_id text NOT NULL REFERENCES "File"(id) ON DELETE CASCADE,
    email text NOT NULL,
    revision integer NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (visitor_id, file_id)
);
CREATE TABLE IF NOT EXISTS "EvolveFileVisit" (
    id uuid PRIMARY KEY,
    file_id text NOT NULL REFERENCES "File"(id) ON DELETE CASCADE,
    visitor_id text NOT NULL,
    email text,
    opened_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evolve_visit_file_time
    ON "EvolveFileVisit"(file_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS evolve_visit_dedup
    ON "EvolveFileVisit"(file_id, visitor_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS evolve_grant_expiry
    ON "EvolveViewGrant"(expires_at);
