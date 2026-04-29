-- TechPulse AI - RBAC Migration (Phase 1)
-- Migrates from single `is_admin` boolean to a 4-role system:
--   'admin'   → Full system control (was is_admin=true)
--   'auditor' → Global read-only access across all tenants
--   'premium' → Advanced features unlocked (50 RSS feeds, webhooks, semantic search, etc.)
--   'user'    → Standard access with quotas (5 RSS feeds, basic dashboard)
--
-- Phase 1: Add new columns + migrate data. `is_admin` is kept as a deprecated alias.
-- Phase 2 (later): DROP COLUMN is_admin after production verification.

-- ── Step 1: Add the role column ──────────────────────────────────────────────
ALTER TABLE tenant_profiles
  ADD COLUMN IF NOT EXISTS role TEXT
    NOT NULL
    DEFAULT 'user'
    CHECK (role IN ('admin', 'auditor', 'premium', 'user'));

-- ── Step 2: Add email column (used by AdminView but missing from schema) ──────
ALTER TABLE tenant_profiles
  ADD COLUMN IF NOT EXISTS email TEXT;

-- ── Step 3: Migrate existing admin users ─────────────────────────────────────
UPDATE tenant_profiles
  SET role = 'admin'
  WHERE is_admin = TRUE AND role = 'user';

-- ── Step 4: Helper function — get role of the currently authenticated user ───
-- Used by all RLS policies below. SECURITY DEFINER so it can read tenant_profiles
-- even when called from within a policy context.
CREATE OR REPLACE FUNCTION get_my_role()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT role
  FROM tenant_profiles
  WHERE user_id = auth.uid()
  LIMIT 1;
$$;

-- ── Step 5: Quota helper — enforces RSS source limits per role ─────────────────
CREATE OR REPLACE FUNCTION can_add_rss_source(p_user_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT CASE
    WHEN get_my_role() IN ('admin', 'premium') THEN
      (SELECT COUNT(*) FROM rss_sources WHERE user_id = p_user_id) < 50
    ELSE
      (SELECT COUNT(*) FROM rss_sources WHERE user_id = p_user_id) < 5
  END;
$$;

-- ── Step 6: Drop old per-table RLS policies and replace with role-aware ones ──

-- tenant_profiles
DROP POLICY IF EXISTS "Tenant isolation - Profiles" ON tenant_profiles;
CREATE POLICY "Profiles - own data or admin/auditor read"
  ON tenant_profiles FOR SELECT
  USING (auth.uid() = user_id OR get_my_role() IN ('admin', 'auditor'));

CREATE POLICY "Profiles - own data write"
  ON tenant_profiles FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Profiles - own data update"
  ON tenant_profiles FOR UPDATE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

CREATE POLICY "Profiles - admin delete only"
  ON tenant_profiles FOR DELETE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

-- articles
DROP POLICY IF EXISTS "Tenant isolation - Articles" ON articles;
CREATE POLICY "Articles - own data or admin/auditor read"
  ON articles FOR SELECT
  USING (auth.uid() = user_id OR get_my_role() IN ('admin', 'auditor'));

CREATE POLICY "Articles - own data write"
  ON articles FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Articles - own data update"
  ON articles FOR UPDATE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

CREATE POLICY "Articles - own data delete"
  ON articles FOR DELETE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

-- rss_sources
DROP POLICY IF EXISTS "Tenant isolation - Sources" ON rss_sources;
CREATE POLICY "Sources - own data or admin/auditor read"
  ON rss_sources FOR SELECT
  USING (auth.uid() = user_id OR get_my_role() IN ('admin', 'auditor'));

CREATE POLICY "Sources - quota-gated insert"
  ON rss_sources FOR INSERT
  WITH CHECK (auth.uid() = user_id AND can_add_rss_source(auth.uid()));

CREATE POLICY "Sources - own data update"
  ON rss_sources FOR UPDATE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

CREATE POLICY "Sources - own data delete"
  ON rss_sources FOR DELETE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

-- article_events
DROP POLICY IF EXISTS "Tenant isolation - Events" ON article_events;
CREATE POLICY "Events - own data or admin/auditor read"
  ON article_events FOR SELECT
  USING (auth.uid() = user_id OR get_my_role() IN ('admin', 'auditor'));

CREATE POLICY "Events - own data write"
  ON article_events FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Events - own data update"
  ON article_events FOR UPDATE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

CREATE POLICY "Events - own data delete"
  ON article_events FOR DELETE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

-- app_config
DROP POLICY IF EXISTS "Tenant isolation - Config" ON app_config;
CREATE POLICY "Config - own data or admin/auditor read"
  ON app_config FOR SELECT
  USING (auth.uid() = user_id OR get_my_role() IN ('admin', 'auditor'));

CREATE POLICY "Config - own data write"
  ON app_config FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Config - own data update"
  ON app_config FOR UPDATE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

-- source_health
DROP POLICY IF EXISTS "Tenant isolation - Stats" ON source_health;
CREATE POLICY "SourceHealth - own data or admin/auditor read"
  ON source_health FOR SELECT
  USING (auth.uid() = user_id OR get_my_role() IN ('admin', 'auditor'));

CREATE POLICY "SourceHealth - own data write"
  ON source_health FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "SourceHealth - own data update"
  ON source_health FOR UPDATE
  USING (auth.uid() = user_id OR get_my_role() = 'admin');

-- user_feedback
DROP POLICY IF EXISTS "Tenant isolation - Feedback" ON user_feedback;
CREATE POLICY "Feedback - own data or admin/auditor read"
  ON user_feedback FOR SELECT
  USING (auth.uid() = user_id OR get_my_role() IN ('admin', 'auditor'));

CREATE POLICY "Feedback - own data write"
  ON user_feedback FOR INSERT
  WITH CHECK (auth.uid() = user_id AND get_my_role() IN ('admin', 'premium', 'user'));

-- telemetry
DROP POLICY IF EXISTS "Tenant isolation - Telemetry" ON telemetry;
CREATE POLICY "Telemetry - own data or admin/auditor read"
  ON telemetry FOR SELECT
  USING (auth.uid() = user_id OR get_my_role() IN ('admin', 'auditor'));

CREATE POLICY "Telemetry - system write"
  ON telemetry FOR INSERT
  WITH CHECK (auth.uid() = user_id OR get_my_role() = 'admin');

-- ── Step 7: Index on role for fast admin queries ───────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tenant_profiles_role ON tenant_profiles(role);

-- ── Verification Queries (run these manually to confirm) ──────────────────────
-- SELECT user_id, full_name, role, is_admin FROM tenant_profiles ORDER BY role;
-- SELECT get_my_role(); -- Run as an authenticated user to test
