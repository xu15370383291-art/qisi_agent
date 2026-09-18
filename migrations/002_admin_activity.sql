-- Read models for the administrator activity pages.
-- These views reuse the existing durable conversation and mistake tables;
-- they do not duplicate user activity.
CREATE INDEX IF NOT EXISTS conversation_messages_user_created_idx
  ON conversation_messages(created_at DESC)
  WHERE role = 'user';

CREATE INDEX IF NOT EXISTS learning_mistakes_admin_created_idx
  ON learning_mistakes(created_at DESC, status);

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
JOIN app_users u ON u.user_id = c.user_id
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
JOIN app_users u ON u.user_id = m.student_id;
