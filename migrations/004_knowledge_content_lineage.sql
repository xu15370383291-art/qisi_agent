-- Knowledge content lineage and import observability.
-- This migration is additive: existing documents and chunks remain valid.

ALTER TABLE public.knowledge_documents
  ADD COLUMN IF NOT EXISTS document_type TEXT NOT NULL DEFAULT '教材',
  ADD COLUMN IF NOT EXISTS current_version_id TEXT;

ALTER TABLE public.knowledge_contents
  ADD COLUMN IF NOT EXISTS version_id TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'knowledge_documents_current_version_fk'
  ) THEN
    ALTER TABLE public.knowledge_documents
      ADD CONSTRAINT knowledge_documents_current_version_fk
      FOREIGN KEY (current_version_id)
      REFERENCES public.knowledge_document_versions(version_id)
      ON DELETE SET NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'knowledge_contents_version_fk'
  ) THEN
    ALTER TABLE public.knowledge_contents
      ADD CONSTRAINT knowledge_contents_version_fk
      FOREIGN KEY (version_id)
      REFERENCES public.knowledge_document_versions(version_id)
      ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS knowledge_documents_current_version_idx
  ON public.knowledge_documents(current_version_id);
CREATE INDEX IF NOT EXISTS knowledge_contents_version_idx
  ON public.knowledge_contents(version_id);
CREATE INDEX IF NOT EXISTS knowledge_document_versions_document_created_idx
  ON public.knowledge_document_versions(document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_jobs_status_created_idx
  ON public.ingestion_jobs(status, created_at DESC);

-- Preserve lineage for rows created before this migration by associating them
-- with the latest known version of their document when one exists.
UPDATE public.knowledge_contents c
SET version_id = (
  SELECT v.version_id
  FROM public.knowledge_document_versions v
  WHERE v.document_id = c.document_id
  ORDER BY v.created_at DESC
  LIMIT 1
)
WHERE c.version_id IS NULL
  AND EXISTS (
    SELECT 1
    FROM public.knowledge_document_versions v
    WHERE v.document_id = c.document_id
  );

UPDATE public.knowledge_documents d
SET current_version_id = (
      SELECT v.version_id
      FROM public.knowledge_document_versions v
      WHERE v.document_id = d.document_id
      ORDER BY v.created_at DESC
      LIMIT 1
    ),
    updated_at = NOW()
WHERE d.current_version_id IS NULL
  AND EXISTS (
    SELECT 1
    FROM public.knowledge_document_versions v
    WHERE v.document_id = d.document_id
  );

CREATE OR REPLACE VIEW public.admin_content_imports AS
SELECT
    j.job_id,
    j.status,
    j.stage,
    j.progress,
    j.retry_count,
    j.error_message,
    j.created_by,
    j.created_at,
    j.started_at,
    j.finished_at,
    v.version_id,
    v.document_id,
    v.file_name,
    v.file_hash,
    v.status AS version_status,
    d.document_name,
    d.document_type,
    d.grade_id,
    d.grade_name
FROM public.ingestion_jobs j
JOIN public.knowledge_document_versions v USING (version_id)
JOIN public.knowledge_documents d USING (document_id);
