-- Runtime business data for production deployments. Knowledge-base tables are
-- managed by qisi_agent.pg_knowledge and are intentionally not duplicated here.
CREATE TABLE IF NOT EXISTS app_users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student','teacher','admin')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS app_users_role_idx ON app_users(role, status);
CREATE UNIQUE INDEX IF NOT EXISTS app_users_username_lower_idx ON app_users(LOWER(username));

CREATE TABLE IF NOT EXISTS app_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS app_sessions_user_idx ON app_sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS learning_conversations (
    session_id TEXT PRIMARY KEY REFERENCES app_sessions(session_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '新学习对话',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES learning_conversations(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('system','user','assistant','tool')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS conversation_messages_session_idx
  ON conversation_messages(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS learning_memories (
    memory_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'learning_event',
    course_id TEXT NOT NULL DEFAULT '',
    knowledge_point_id TEXT NOT NULL DEFAULT '',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.8 CHECK (confidence BETWEEN 0 AND 1),
    importance DOUBLE PRECISION NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_message_id TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','active','updated','deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS learning_memories_student_idx ON learning_memories(student_id, status, occurred_at DESC);
CREATE INDEX IF NOT EXISTS learning_memories_point_idx ON learning_memories(knowledge_point_id);

CREATE TABLE IF NOT EXISTS learning_mistakes (
    mistake_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    student_answer TEXT NOT NULL DEFAULT '',
    correct_answer TEXT NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    knowledge_point_id TEXT NOT NULL DEFAULT '',
    source_message_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unreviewed' CHECK (status IN ('unreviewed','reviewed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS learning_mistakes_student_idx ON learning_mistakes(student_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS practice_quizzes (
    quiz_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    grade_id TEXT NOT NULL CHECK (grade_id IN ('grade7','grade8','grade9')),
    submitted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS practice_questions (
    question_id TEXT NOT NULL,
    quiz_id TEXT NOT NULL REFERENCES practice_quizzes(quiz_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    prompt TEXT NOT NULL,
    options JSONB NOT NULL,
    answer_index INTEGER NOT NULL CHECK (answer_index >= 0),
    explanation TEXT NOT NULL DEFAULT '',
    citation JSONB NOT NULL DEFAULT '{}'::jsonb,
    knowledge_point TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (quiz_id, question_id),
    UNIQUE (quiz_id, ordinal)
);
CREATE TABLE IF NOT EXISTS practice_attempts (
    attempt_id BIGSERIAL PRIMARY KEY,
    quiz_id TEXT NOT NULL REFERENCES practice_quizzes(quiz_id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    student_id TEXT NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    selected_index INTEGER,
    correct_index INTEGER NOT NULL,
    is_correct BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (quiz_id, question_id),
    FOREIGN KEY (quiz_id, question_id) REFERENCES practice_questions(quiz_id, question_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS practice_attempts_student_idx ON practice_attempts(student_id, created_at DESC);

-- Curated/generated question bank. One row is a reusable question, while
-- practice_questions remains the per-quiz snapshot used for submissions.
CREATE TABLE IF NOT EXISTS practice_question_bank (
    question_id TEXT PRIMARY KEY,
    grade_id TEXT NOT NULL CHECK (grade_id IN ('grade7','grade8','grade9')),
    knowledge_point TEXT NOT NULL,
    chapter TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL,
    options JSONB NOT NULL,
    answer_index INTEGER NOT NULL CHECK (answer_index >= 0),
    explanation TEXT NOT NULL DEFAULT '',
    citation JSONB NOT NULL DEFAULT '{}'::jsonb,
    difficulty TEXT NOT NULL DEFAULT '基础',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS practice_question_bank_grade_point_idx
  ON practice_question_bank(grade_id, knowledge_point, active);

CREATE TABLE IF NOT EXISTS learning_practice_stats (
    student_id TEXT NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    knowledge_point_id TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    correct INTEGER NOT NULL DEFAULT 0 CHECK (correct >= 0 AND correct <= attempts),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (student_id, knowledge_point_id)
);

CREATE TABLE IF NOT EXISTS agent_checkpoints (
    session_id TEXT PRIMARY KEY REFERENCES app_sessions(session_id) ON DELETE CASCADE,
    state JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS agent_checkpoints_updated_idx ON agent_checkpoints(updated_at DESC);

CREATE TABLE IF NOT EXISTS runtime_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS runtime_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
