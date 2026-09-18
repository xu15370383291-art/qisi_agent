# 阶段验收记录

## 知识库链路

- 范围：七年级数学示例语料，包含分数和一元一次方程两个知识点。
- 数据 Schema：`Chunk`、`RetrievalHit`、`Citation`、`MemoryItem` 已结构化定义。
- 资料导入：教材支持 Markdown/TXT，题库支持 JSON/JSONL/CSV，并统一生成 `question_id`、章节、知识点、难度和来源元数据。
- 知识库导入：`pg-import` 将 Chunk、知识点和文档元数据写入 PostgreSQL。
- 向量生成：`pg-embed` 使用百炼 Embedding 写入 `knowledge_content_embeddings`，并由 pgvector HNSW 索引支持检索。

## Agent 与服务

- 文档解析和标题感知切分：已实现。
- 百炼向量 + BM25 + RRF + Cross-Encoder：已接入 PostgreSQL 生产链路。
- 引用、来源标注和 SSE 事件：已实现；知识库外问题会明确标注通用知识来源。

## 阶段 2

- `Supervisor` 根据规则路由答疑、错题、出题和学情意图。
- `CheckpointStore` 保存会话状态，`GET /api/conversations/{session_id}` 可回读。
- `MemoryStore` 支持短期会话、长期事件抽取、相似去重、教师审核/修正/删除和学生画像。
- SSE 包含 `node_started`、`memory_hit`、`retrieval`、`citation`、`answer_delta`、`error`、`done` 事件。

## 阶段 3

按当前范围暂缓知识图谱和 Neo4j 接入，`KnowledgeGraph` 只保留离线验证代码，不纳入当前运行链路。

## 阶段 4

- 用户认证：SQLite 用户表、PBKDF2 密码哈希、签名 Bearer 令牌、注册/登录/当前用户接口已接入；注册和登录均区分学生、教师、管理员角色，公开注册禁止创建管理员。
- 数据隔离：聊天会话绑定用户；学生只能读取自己的画像和记忆，教师看板与记忆审核需要教师/管理员角色。
- 管理后台：`/admin` 提供系统概览和用户角色/状态管理；`promote-admin` 用于首次初始化管理员账号。

- 教师看板：`GET /api/teacher/overview` 聚合学生数、学习线索、待审核数和知识点。
- 前端已增加“教师看板”视图，展示班级学生状态和近期知识点。
- 教师可在看板中确认待审核记忆，并用检索调试面板查看召回候选、章节和分数。
- 服务模式下长期记忆写入 `data/runtime/memory.json`，重启后保留；短期会话仍按 TTL 管理。
- 学习记忆的 PostgreSQL/Redis 持久化、刷新令牌和真实教育领域验收集仍待接入；知识库 PostgreSQL + pgvector 已接入。
