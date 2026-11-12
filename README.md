# hh-match-agent 猎头简历-岗位匹配 Agent

顾问上传候选人简历（PDF/DOCX/TXT），Agent 对已选项目自动匹配，输出：

1. **岗位逐项匹配明细**：硬性条件逐项匹配 + 优先条件逐项匹配，标签为【满足】/【不满足】/【待确认】
2. **硬性条件得分 + 优先条件得分 + 综合参考得分**，推荐级别口径统一为：≥90分建议推荐客户，≥80分进入人工审核
3. **专业简历优化沟通建议**（简历补充 / 简历强调 / 沟通确认）

核心资产是**三层提示词**（通用 / 公司 / 岗位专属），PM 在页面上维护，运行时自动组装，替代每天手动改 prompt。

## 正式使用方式

只部署一套共享服务。团队用户访问同一个线上/内网 URL，使用 Agent 自己的账号体系登录；后端统一使用 `.env` 中配置的飞书应用凭证连接同一个多维表格 Base，同步客户、岗位、项目、提示词、匹配结果和进程数据。

### 部署

```bash
# 1. 构建前端产物，由 FastAPI 统一托管
cd frontend
npm install
npm run build

# 2. 配置共享服务后端
cd ../backend
cp .env.example .env
# 填入：
# - APP_PUBLIC_URL：团队访问的唯一地址
# - OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
# - FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_BASE_TOKEN / FEISHU_TABLE_*

# 3. 启动共享后端
./run.sh
```

打开 `.env` 中的 `APP_PUBLIC_URL` → 登录 / 注册 → 发起匹配 → 上传简历 → 选择项目 → 开始匹配。生产环境建议通过 Nginx、SLB 或内网网关把 `APP_PUBLIC_URL` 转发到后端 `HOST:PORT`，并把 `backend/data` 放在持久化磁盘上。

### 本地开发

开发时仍可分别启动前后端，前端 dev server 会把 `/api` 代理到后端：

```bash
cd backend
./run.sh

cd ../frontend
npm install
npm run dev
```

### 切换 DeepSeek 官方接口

`.env` 里配置：

```bash
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

匹配链路会继续使用同一套三层提示词：通用猎头 prompt 进入 system，公司特定 prompt 和岗位特定 prompt 按顺序进入明细阶段 user prompt；JSON 输出调用会自动开启 JSON mode。

岗位管理、客户管理中的文件导入同样走这套 LLM 配置：上传 PDF/DOCX/XLSX/TXT 后，后端先抽取文本，再调用 DeepSeek/OpenAI 兼容接口结构化并入库；岗位 Excel 会优先按表头结构化拆分后入库。飞书多维表格同步由共享后端统一执行，不需要每个用户各自连接飞书。

## 架构

```
backend/app/
├── routers/      candidates 简历上传 · positions 岗位库 · prompts 三层提示词
│                 matching 匹配 run · companies 客户管理 · tracking 进程管理
├── services/
│   ├── matcher.py          已选项目逐个明细 → 按条数计分 → 落库
│   ├── prompt_composer.py  三层组装；输出骨架代码固定，PM 只改业务层
│   ├── jd_extractor.py     JD → 结构化字段 + 原子化 requirements（jd_hash 缓存）
│   ├── resume_parser.py    pdfplumber + 中文简历分节（扫描件报错，不做 OCR）
│   └── report.py           结果 → 报告 JSON + markdown（纯 Python）
└── prompts/      种子提示词：通用层默认值 + 明细输出骨架 + few-shot
```

共享服务使用 SQLite（`backend/data/hh.db`）保存账号会话、上传文件、匹配过程和飞书同步缓存。客户、岗位、项目、提示词、匹配结果和进程数据会按配置同步到同一个飞书 Base；每次 run 会保存 prompt 快照与模型原始输出，便于后续持续学习和反驳验证。

## 测试

```bash
cd backend
./.venv/bin/python -m pytest tests/ -q          # 单测（无 LLM）
SMOKE_USERNAME=xxx SMOKE_PASSWORD=xxx ./.venv/bin/python scripts/smoke_e2e.py 某简历.pdf
```

E2E 需要后端已启动、`.env` 配好 LLM 和飞书，并使用一个已有 Agent 账号登录。它会验证报告形态、标签白名单、阈值映射，并给最高分岗位加一条硬性排除规则重跑，断言分数下降（三层提示词有效性）。

## 明确不做（MVP 边界）

OCR 扫描件、流式进度（轮询代替）、JD 变化自动重匹配（jd_hash 已留钩子）、标签体系 / 薪酬预测 / RAG 隐性需求挖掘 / 反馈学习（数据钩子已留）。
