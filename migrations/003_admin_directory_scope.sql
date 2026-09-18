-- Keep generated migration/session identities out of administrator-facing
-- user and activity lists. Their original records remain intact for audit
-- and historical data preservation.
CREATE OR REPLACE VIEW admin_user_directory AS
SELECT user_id, username, display_name, role, status, created_at
FROM app_users
 WHERE username NOT LIKE 'migrated%'
  AND username NOT LIKE 'runtime%';

CREATE INDEX IF NOT EXISTS learning_mistakes_admin_point_idx
  ON learning_mistakes(knowledge_point_id, student_id, created_at DESC);

CREATE OR REPLACE VIEW admin_question_activity AS
SELECT
    m.message_id,
    c.session_id,
    c.title AS session_title,
    c.user_id,
    u.username,
    u.display_name,
    u.role AS user_role,
    m.content AS question,
    m.created_at
FROM conversation_messages m
JOIN learning_conversations c ON c.session_id = m.session_id
JOIN admin_user_directory u ON u.user_id = c.user_id
WHERE m.role = 'user';

CREATE OR REPLACE VIEW admin_mistake_activity AS
SELECT
    m.mistake_id,
    m.student_id AS user_id,
    u.username,
    u.display_name,
    u.role AS user_role,
    m.prompt,
    m.student_answer,
    m.correct_answer,
    m.explanation,
    m.knowledge_point_id,
    m.source_message_id,
    m.status,
    m.created_at,
    m.reviewed_at
FROM learning_mistakes m
JOIN admin_user_directory u ON u.user_id = m.student_id;
