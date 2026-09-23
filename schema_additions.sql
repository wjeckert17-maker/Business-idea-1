-- Additions to schema.sql needed by the ingestion job.
-- Sections often carry a title that differs from the catalog ("Special Topics: Robot Ethics").
ALTER TABLE section ADD COLUMN IF NOT EXISTS title text;

-- Needed by reqx (catalog -> requirement tree). A NEEDS_REVIEW group is a placeholder a human must
-- resolve; it has no children and must never be evaluated as satisfied.
ALTER TABLE requirement_group ADD COLUMN IF NOT EXISTS needs_review boolean NOT NULL DEFAULT false;
ALTER TABLE requirement       ADD COLUMN IF NOT EXISTS source_text text;     -- verbatim catalog line(s)
ALTER TABLE requirement       ADD COLUMN IF NOT EXISTS aliases text[];       -- cross-listed codes for the same course
