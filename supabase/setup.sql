-- ============================================================
-- SpendSense — Supabase setup SQL
-- Run this once in the Supabase SQL editor after deploying
-- the app for the first time (so SQLAlchemy has created the tables).
--
-- Architecture note:
--   FastAPI connects to Postgres directly via the service role
--   (DATABASE_URL connection string). This bypasses Supabase RLS,
--   so user isolation is enforced in FastAPI code instead.
--   Do NOT enable the Data API — we use our own FastAPI backend.
-- ============================================================

-- ── Indexes for fast per-user queries ────────────────────────
-- These matter once you have many users; run them on first deploy.

CREATE INDEX IF NOT EXISTS idx_transactions_user_id    ON transactions   (user_id);
CREATE INDEX IF NOT EXISTS idx_budget_targets_user_id  ON budget_targets (user_id);
CREATE INDEX IF NOT EXISTS idx_merchant_rules_user_id  ON merchant_rules (user_id);

-- ── Supabase dashboard checklist ─────────────────────────────
-- Authentication → Providers → Google → enable + add Client ID/Secret
-- Authentication → Settings → "Enable email confirmations" → OFF (for easier dev)
-- API settings → Data API → leave DISABLED (we use FastAPI, not PostgREST)
-- RLS → leave DISABLED (FastAPI enforces user_id scoping on every query)
