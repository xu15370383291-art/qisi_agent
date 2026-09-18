# 启思学伴智能教育 Agent

本仓库按《启思学伴智能教育 Agent 项目规划》分阶段落地。生产链路固定为：教材/题库解析、百炼 Embedding、PostgreSQL + pgvector、BM25/RRF、Cross-Encoder 重排和 Qwen 生成；认证、会话、学习记忆、错题、练习提交和 Agent checkpoint 也统一持久化到 PostgreSQL。

## 快速开始

```powershell
python -m pip install -e ".[full]"
Copy-Item .env.example .env
python -m qisi_agent.cli pg-import --source data/corpus
python -m qisi_agent.cli pg-embed
python -m qisi_agent.cli pg-search --query "分数怎样约分" --grade grade7
python -m qisi_agent.cli serve
```

`data/corpus` 支持 Markdown/TXT 教材，以及结构化题库 `JSON/JSONL/CSV`。题库字段可使用 `question_id`、`question`、`answer`、`explanation`、`knowledge_point`、`chapter`、`difficulty`（也兼容常见中文字段名）；加入新资料后重新执行 `pg-import` 和 `pg-embed`，再重启服务使新内容生效。

## 知识库和模型

安装完整依赖（包含 Cross-Encoder reranker）并复制环境变量模板：

```powershell
python -m pip install -e ".[cloud,reranker]"
Copy-Item .env.example .env
```

将百炼 Key 和 PostgreSQL 连接信息写入 `.env` 后，先导入知识库并生成 pgvector：

```powershell
python -m qisi_agent.cli pg-import --source data/corpus
python -m qisi_agent.cli pg-embed
python -m qisi_agent.cli db-migrate-runtime
python -m qisi_agent.cli serve
```

服务会使用百炼向量、BM25、加权 RRF 和 Cross-Encoder；回答由 `CHAT_MODEL`（默认 `qwen-plus`）生成。Key 缺失、数据库连接失败、模型不一致或向量维度不匹配时会明确报错，不会回退到本地 JSON 索引。

若安装了 `fastapi` 和 `uvicorn`，可启动 SSE API：

```powershell
python -m qisi_agent.cli serve
# 端口被占用时：
python -m qisi_agent.cli serve --port 8001
```

项目会自动读取 `.env`；服务启动时从 PostgreSQL 读取已发布内容和向量，不再加载本地 JSON 索引。

首次上线前运行 `db-migrate-runtime`，将历史 `data/runtime/auth.sqlite3` 与 `data/runtime/memory.json` 幂等导入 PostgreSQL；命令不会删除源文件。生产 `serve` 默认使用 PostgreSQL 运行时存储。首次打开首页会先注册或登录，并明确选择账号类型。公开注册只允许学生或教师；管理员账号不能自助注册，需由现有管理员使用初始化命令创建。密码不会明文保存；认证签名密钥由 `AUTH_SECRET_KEY` 配置，未配置时会保存在 PostgreSQL `runtime_settings` 表。学生只能访问自己的学习数据，教师看板和记忆审核接口需要教师/管理员角色。

管理员可访问 `/admin/login`，点击“申请管理员账号”，填写账号、密码和管理员申请码。当前申请码由服务端 `.env` 中的 `ADMIN_REGISTRATION_CODE` 配置（本地值为用户指定的申请码）；只有服务端校验通过才会创建管理员。学生和教师账号不能从管理员入口登录。原有 `promote-admin` 命令仍可用于维护已有账号。
管理员账号访问学习端 `/` 时会自动跳转到 `/admin`，聊天、会话和学习画像等学生业务接口也会拒绝管理员请求。管理员后台首页只负责用户管理；错题记录、提问记录和教材/题库导入分别位于左侧导航的独立页面。管理员可通过 `GET /api/admin/mistakes` 查看学生错题，通过 `GET /api/admin/questions` 查看学生在学习对话中的提问。

接口：`POST /api/auth/register`、`POST /api/auth/login`、`GET /api/auth/me`、`POST /api/chat/stream`、`GET /api/conversations/{session_id}`、`GET /api/retrieval/debug`、`GET /api/students/{student_id}/learning-profile`、`GET /api/students/{student_id}/memories`、`GET /api/teacher/overview`、`POST /api/memories/{id}/review`、`GET /health`。知识库和运行时业务数据统一使用 PostgreSQL + pgvector；本地 SQLite/JSON 仅作为迁移前备份，不再被生产服务读取。

学习工作台支持在顶部切换七年级、八年级、九年级；选择会按用户保存在浏览器中，并通过 `grade_id` 传给聊天和检索调试接口，避免不同年级知识混合召回。当前课程范围固定为数学，后续扩展其他学科时可复用 `course_id` 过滤字段。

练习中心支持按当前年级生成教材选择题，学生提交后获得得分、逐题解析和教材引用；答错的知识点会自动写入学习记忆。接口为 `GET /api/practice/quiz` 和 `POST /api/practice/submit`。

题库记录支持 `options`、`answer`/`answer_index`、`explanation` 和 `difficulty` 字段；练习优先使用真实题库题目，可按难度筛选，并会按知识点累计答题正确率用于学习画像。教师/管理员可在管理后台上传教材或题库并查看导入任务状态。

检索采用动态混合路由：知识点名称作为检索文档字段参与 BM25；明确概念或关键词较多的问题提高 BM25 权重和候选量，自然语言/语义问题提高向量权重和候选量，平衡型问题使用 50/50 融合。PostgreSQL 合并候选后，使用 `BAAI/bge-reranker-base` Cross-Encoder 对 `(问题, 候选正文)` 联合打分并决定最终顺序。可通过 `RERANKER_MODEL`、`RERANKER_DEVICE`、`RERANKER_BATCH_SIZE` 和 `RERANKER_MAX_LENGTH` 调整模型与推理参数。

Cross-Encoder 首次启动需要从 Hugging Face 下载模型。如果当前网络无法访问 Hugging Face，服务默认会保留混合检索结果并跳过二次重排，同时输出警告；设置 `RERANKER_REQUIRED=true` 可改为模型不可用时直接报错，设置 `RERANKER_ENABLED=false` 可主动关闭模型重排。也可以将 `RERANKER_CACHE_DIR` 指向本地模型目录，并设置 `RERANKER_LOCAL_FILES_ONLY=true` 完全离线运行。网络恢复后重启服务即可自动重新尝试加载模型。

PostgreSQL 混合检索命令：

```powershell
.venv\Scripts\python.exe -m qisi_agent.cli pg-import --source data/corpus
.venv\Scripts\python.exe -m qisi_agent.cli pg-embed
.venv\Scripts\python.exe -m qisi_agent.cli pg-search --query "分数怎样约分" --grade grade7
.venv\Scripts\python.exe -m qisi_agent.cli serve
```

文档异步处理使用 `knowledge_document_versions` 和 `ingestion_jobs` 记录版本、阶段和失败重试信息；后续上传接口与 Worker 将基于这两张表扩展。

## 分阶段状态

- 知识库：`data/corpus`、`qisi_agent/ingestion.py`、`qisi_agent/pg_knowledge.py`、`qisi_agent/postgres_retrieval.py`
- 阶段 2：`qisi_agent/memory.py`、`qisi_agent/agents.py`、`qisi_agent/api.py`（本地 Supervisor、Checkpoint、会话和记忆管理）
- 阶段 3：暂缓，`qisi_agent/graph.py` 仅保留离线验证代码
- 阶段 4：教师看板、持久化记忆、`tests/` 和 `docs/acceptance.md`

阶段 2/3 的本地实现是可测量的验证骨架，不宣称已经连接外部 LangGraph/Neo4j。接入时可按模块接口替换存储和编排实现。
