-- Run this once in the Supabase SQL Editor for the MBR project.

-- ── Sessions ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    practice_name   TEXT,
    month           INTEGER,
    year            INTEGER,
    data            JSONB NOT NULL,
    brand_bank_path TEXT,
    marketing_image_path TEXT,
    launches_image_path  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sessions_practice_month_year
    ON sessions (practice_name, year, month);

-- Auto-update updated_at on every row change
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sessions_updated_at ON sessions;
CREATE TRIGGER sessions_updated_at
    BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ── Version history ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS session_versions (
    id              BIGSERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    data            JSONB NOT NULL,
    brand_bank_path TEXT,
    marketing_image_path TEXT,
    launches_image_path  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS session_versions_session_id_idx
    ON session_versions (session_id, created_at DESC);


-- ── Monthly assets ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monthly_assets (
    period          TEXT PRIMARY KEY,   -- e.g. "2026-05"
    month           INTEGER NOT NULL,
    year            INTEGER NOT NULL,
    launches        JSONB DEFAULT '[]',
    brand_bank_items JSONB DEFAULT '[]',
    launches_file   TEXT,
    brand_bank_file TEXT,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

DROP TRIGGER IF EXISTS monthly_assets_updated_at ON monthly_assets;
CREATE TRIGGER monthly_assets_updated_at
    BEFORE UPDATE ON monthly_assets
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ── Storage buckets (run separately or via Supabase dashboard) ────────────────
-- Create a bucket named "monthly-assets" with public=false in the Storage tab.
-- INSERT INTO storage.buckets (id, name, public) VALUES ('monthly-assets', 'monthly-assets', false)
-- ON CONFLICT (id) DO NOTHING;
