-- Learning-memory lifecycle.  Records are never silently overwritten: a new
-- observation can supersede or conflict with an older observation, and the
-- evidence that caused the transition remains queryable.

ALTER TABLE public.learning_memories
    ADD COLUMN IF NOT EXISTS semantic_key TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS supersedes_memory_id TEXT REFERENCES public.learning_memories(memory_id),
    ADD COLUMN IF NOT EXISTS status_reason TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_reinforced_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

-- Older installations have an anonymous CHECK constraint generated from the
-- original CREATE TABLE statement.  Replace only the constraint that checks
-- the status column, leaving unrelated checks untouched.
DO $$
DECLARE constraint_name TEXT;
BEGIN
    FOR constraint_name IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.learning_memories'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%status%'
    LOOP
        EXECUTE format('ALTER TABLE public.learning_memories DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END $$;

ALTER TABLE public.learning_memories
    ADD CONSTRAINT learning_memories_status_check
    CHECK (status IN ('candidate','active','updated','superseded','stale','archived','conflict','deleted'));

CREATE INDEX IF NOT EXISTS learning_memories_student_semantic_idx
    ON public.learning_memories(student_id, semantic_key, status, occurred_at DESC);

CREATE TABLE IF NOT EXISTS public.learning_memory_evidence (
    evidence_id BIGSERIAL PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES public.learning_memories(memory_id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    evidence_role TEXT NOT NULL DEFAULT 'supports',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (evidence_role IN ('supports','supersedes','contradicts','reinforces','extraction'))
);
CREATE INDEX IF NOT EXISTS learning_memory_evidence_memory_idx
    ON public.learning_memory_evidence(memory_id, created_at DESC);

-- This is intentionally a compact current-state projection.  Long-term
-- evidence remains in learning_memories; this table makes a student's latest
-- knowledge-point state cheap and explicit to read.
CREATE TABLE IF NOT EXISTS public.student_knowledge_states (
    student_id TEXT NOT NULL REFERENCES public.app_users(user_id) ON DELETE CASCADE,
    knowledge_point_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('unknown','learning','mastered','needs_review','conflict')),
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    active_memory_id TEXT REFERENCES public.learning_memories(memory_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (student_id, knowledge_point_id)
);
