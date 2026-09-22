-- Versioned JWTs allow a password change or "sign out everywhere" action to
-- invalidate every previously issued token without retaining token contents.
ALTER TABLE public.app_users
    ADD COLUMN IF NOT EXISTS auth_token_version INTEGER NOT NULL DEFAULT 1 CHECK (auth_token_version > 0);
