# 资助政策智能体 · 系统现状盘点（工程视图）

**日期**：2026-11-21
**类型**：利益相关者更新（系统技术栈 / 板块 / 板块内容盘点）
**参与成员**：本次为代码事实审查，由主理人汇编；五路只读审查覆盖后端核心、判等工具链、咨询问答、前端、数据与部署测试，并对路由 / 依赖 / 数据库行数做原文交叉核实。
**审查方式**：全仓库只读审查，排除 `zizhuagent/`（6437 文件的本地 Python 环境）；所有数字来自代码或数据库只读查询，未做任何推测。

---

## 📌 TL;DR（执行摘要）

- **核心目标**：按你的开发习惯（结构化、精确到路径行号、严禁编造、先事实后结论），盘清这套系统"用了什么技术、有哪些板块、各板块什么内容"。
- **关键判断**：这是一套**零前端框架、几乎零第三方依赖**的 FastAPI + SQLite + 原生多页 HTML 系统；判等与协同全部是确定性规则代码，模型只做措辞组织且被事实守恒校验锁死。
- **板块格局**：8 个页面（学生 4 + 教师 1 + 管理 1 + 登录 1 + 闯关 1）、60+ API、2 个 SQLite 库共 22 张表、7 组测试脚本。
- **下一步**：核对"未验收项"（题面人工签认、真机试玩、公网部署）——这些是账本里明确标 NOT_RUN 的边界。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 推荐方案 | 按本报告的"技术栈 → 板块 → 板块内容"三层结构理解系统 |
| 优先级 | P0（现状盘点，所有后续决策的事实底座） |
| 预期影响 | 让技术构成、板块边界、每块职责可核对，避免凭印象描述系统 |
| 资源需求 | 无（本次为只读审查 + 文档存档，未改任何源代码） |
| 风险等级 | 低（报告内所有结论均附代码路径或数据库查询证据） |

---

## 一、系统用了什么技术

### 1.1 技术栈总表

| 层次 | 技术 | 证据 / 说明 |
|---|---|---|
| 后端框架 | **FastAPI 0.115.x** | `Dockerfile` pip 安装；`backend/app/main.py` 用 `@app.get/post` 注册路由 |
| ASGI 服务器 | **uvicorn[standard] 0.30.x** | `Dockerfile` CMD 启动 `backend.app.main:app` |
| 数据库 | **SQLite（标准库 sqlite3）** | 两个库：`data/app.db`（业务）、`data/staging/policies.db`（政策）；`import` 统计 sqlite3 出现 14 次 |
| PDF 解析 | **pypdf 5.x** | `Dockerfile` 唯一第三个第三方包，用于政策文档切块 |
| 前端 | **原生 HTML + CSS + JavaScript，零框架** | 8 个 HTML、5 个 CSS、2 个 JS；`frontend/` 下无任何 Vue/React/打包配置 |
| 模板 | **Jinja2（FastAPI 内置）** | `HTMLResponse` + `read_text` 直出页面 |
| 会话 | **HttpOnly Cookie `fund_session`** | `auth.py`；页面不通过前端 `X-Role`/`X-User` 头声明身份 |
| 口令存储 | **PBKDF2-HMAC-SHA256，20 万次迭代 + 随机盐** | `accounts.py:47-50` `_hash()` |
| 模型接入 | **OpenAI 兼容 chat/completions（可选）** | `provider.py`：`FUND_LLM_BASE/KEY/MODEL` 环境变量；默认 offline |
| 部署 | **Docker + docker compose，Python 3.11-slim 基础镜像** | `Dockerfile`、`docker-compose.yml`、`docker-compose.server.yml` |

### 1.2 一条重要事实：依赖极度精简

- 全仓库 **没有** `requirements.txt` / `pyproject.toml` / `setup.py`，依赖只在 `Dockerfile` 里声明三个：`fastapi`、`uvicorn[standard]`、`pypdf`。
- 其余全部是 Python 标准库：`json`、`urllib`、`sqlite3`、`hashlib`、`pathlib`、`re`、`datetime`、`tempfile`、`argparse`、`secrets`、`zipfile` 等（来自 backend/ 与 tests/ 的 import 统计）。
- 意义：窄依赖 = 攻击面小、部署轻；代价是能力都靠自己写（判等、脱敏、会话、审计均自研）。

### 1.3 三条不可动摇的设计约束（来自 AGENTS.md）

| 编号 | 约束 | 落到代码的表现 |
|---|---|---|
| I-1 | 无条款不出结论 | 判等条目至少挂 1 条有效期内条款，否则返回"未匹配，咨询资助中心" |
| I-2 | 三档结论由程序给出 | `eligible`/`need_check`/`not_applicable` 由 `triage_one()` 算；模型只解释已有结果，不说"符合/不符合" |
| I-3 | 引用由系统拼装 | `reasons` 带 `clause_id` + 原文偏移，可点回原文；`game_routes.py` 原样透传 |

---

## 二、系统有哪些板块（模块划分）

按"数据 → 判等 → 能力 → 接口 → 页面 → 验证"分层，共 7 个板块：

| # | 板块 | 主要目录/文件 | 一句话职责 |
|---|---|---|---|
| 1 | 数据底座 | `data/`、两个 SQLite 库 | 政策原文、条款、原子条件、字段字典、资助项目、业务记录 |
| 2 | 政策治理 | `backend/app/ingest.py`、`backend/tools/policy_collector.py`、`atomize.py` | 采集→切块→抽原子→核文→发布→快照→回滚 |
| 3 | 资格判等 | `backend/tools/triage.py`、`data/atoms/`、`data/fields.json` | 把学生画像翻译成三档结论 + 依据 |
| 4 | AI 能力层 | `backend/app/provider.py`、`agents.py`、`logs.py` | 三态模型接入 + 六代理协同 + 日志脱敏 |
| 5 | 业务接口 | `backend/app/main.py`、各 `*_routes.py` | 60+ REST API，覆盖初判/审核/政策/问答/账号/统计 |
| 6 | 前端页面 | `frontend/` | 8 个页面的交互与展示 |
| 7 | 验证与运维 | `tests/`、`backend/scripts/`、`docs/verification.md` | 测试、备份恢复、隐私扫描、事实台账 |

---

## 三、各板块具有哪些内容（逐块展开）

### 板块 1 · 数据底座

| 数据资产 | 实际内容（只读查询所得） |
|---|---|
| `data/app.db`（业务库） | 15 张表：`app_user`(6)、`triage_task`(15)、`policy_version`(1)、`qa_session`(0)、`qa_turn`(0)、`audit_log`(93)、`material_item`(16)、`appeal`、`review_event`、`school_tier`、`user_session`、`qa_handoff`、`export_request`、`policy_verify` + `sqlite_sequence` |
| `data/staging/policies.db`（政策库） | 7 张表：`policy_staging`(19 份)、`policy_clause`(342 条)、`policy_atom`(30)、快照表 `policy_snapshot_clause`(93)、`policy_snapshot_atom`(30)、`crawl_run`、`sqlite_sequence` |
| `data/fields.json` | 16 个字段的字段字典（v2），字段不在字典内不得入库 |
| `data/atoms/core3_atoms.json` | 30 条原子条件，覆盖 5 个项目 |
| `data/projects.json` | 5 个资助项目 |
| 发布状态 | 19 份政策中 3 份核心已核文发布，16 份仍 `pending_verify`；全局 `policy_verified=false` |

> 说明：`qa_session`/`qa_turn` 表结构存在且代码会落库，但当前库里行数为 0——如实记录，不写成"已有咨询数据"。

### 板块 2 · 政策治理

- `policy_collector.py`：只采白名单官方栏目页，默认间隔 3 秒、单线程；子命令 `crawl` / `split` / `verify-clauses` / `gui`。采到的是草稿（`status=pending_verify`、`publishable=0`），文号与生效日期须人工核对（I-7）。
- `ingest.py` + `atomize.py`：文档入库 → 条款切块 → 原子抽取；`atomize.py` 提供 `import` / `verify` / `coverage` / `export` / `regress` 五个子命令，`regress` 是改字段字典后的必跑项。
- 发布采用**版本快照**机制：发布时写快照，检索与判等读快照，回滚真正切换内容（`tests/version_flow.py` 10 项验证）。

### 板块 3 · 资格判等（系统核心）

- `triage.py` 关键函数：`load_atoms()`、`load_projects()`、`validate_student()`、`triage_one()`。
- 三档结论：`eligible` 可申请 / `need_check` 需核实 / `not_applicable` 暂不适用（中文标签在 `CONCLUSION_LABEL`）。
- 判等逻辑：按原子条件（字段 + 运算符 + 取值 + 原文片段）组合；缺字段 → `need_check`；`manual` 类人工原子（如"已获其他专项"）触发降级；`superseded`（已替代）原子保留备查但不参与判等。
- 输出结构：每条结论含 `conclusion`、`reasons`（带 `clause_id` 与原文偏移）、`missing_fields`、`materials`、`notes`。
- 6 个闯关关卡的画像全部经 `triage_one()` 实跑确认（L1 暂不适用 / L2 需核实 / L3 可申请 / L4 暂不适用 / L5 可申请 / L6 需核实）。

### 板块 4 · AI 能力层

| 组件 | 内容 |
|---|---|
| `provider.py` 三态 | `offline`（默认，模板拼条款，功能不缩减）、`online`（OpenAI 兼容端点，输出过结构校验+业务校验，失败重试 1 次再回退离线并记录 last_error）、`mock`（自测） |
| 事实守恒校验 | 数字/金额/文号/条款号必须是 FactPack 给定事实的子集，超集即拒；结论三档守恒、措辞与敏感信息检查（ADR-0002，12/12 自测含编造注入负样本） |
| `agents.py` 六代理 | **全部是规则型确定性代码，零模型参与**：受理→判等→依据核验→表述→合规→汇总；每步留 `agent/seq/ms/status/summary` 可核对 |
| `logs.py` 脱敏 | 手机号、身份证、银行卡、邮箱、姓名字段在写审计前脱敏（I-4）；自测通过 |

### 板块 5 · 业务接口（60+ API）

按职责分组（路径均来自 `main.py` 原文行号）：

| 接口组 | 代表路由 | 覆盖范围 |
|---|---|---|
| 页面路由 | `/`、`/triage.html`、`/qa.html`、`/progress.html`、`/game.html`、`/review.html`、`/admin.html`、`/login.html` | 8 个页面 |
| 辅助 | `/api/whoami`、`/api/fields`、`/api/samples`、`/api/health` | 字段字典、样例、健康 |
| 初判与复核 | `/api/triage`、`/api/triage/{id}/submit`、`/review`、`/appeal`、`/api/my-tasks`、`/api/tasks` | 初判→提交→复核→申诉 |
| 看板与统计 | `/api/board`、`/api/board/levels`、`/api/warnings`、`/api/report/summary`、`/api/report/export.csv` | 两级看板、预警、报送 |
| 政策治理 | `/api/policy/docs`、`/upload`、`/{id}/split`、`/verify`、`/publish`、`/api/policy/versions`、`/rollback`、`/search` | 采集到发布回滚全链 |
| 咨询问答 | `/api/qa`（单轮兼容）+ `qa_routes` 的 `/api/qa/sessions`、`/turns`、`/handoff`、`/evidence`、`/export`、`/events` | 会话化多轮咨询，会话/轮次/交接落 `qa_*` 表 |
| 账号与权限 | `/api/auth/register`、`/login`、`/logout`、`/me`、`/change-password`、`/api/admin/registrations`、`/api/admin/users/{id}/reset-password` | 注册审核、改密重置 |
| 管理 | `/api/users`、`/enable`、`/api/materials`、`/api/school-tiers`、`/api/audit`、`/api/export-requests` | 账号启停、材料、分档、审计、导出审批 |
| 闯关 | `/api/game/levels`、`/api/game/answer` | 题干与答案分离，答案由 `triage_one()` 现算 |

### 板块 6 · 前端页面（8 个）

| 页面 | 端 | 主要内容 | 身份 |
|---|---|---|---|
| `index.html` | 学生 | 隐私授权勾选、原子字段表单、三档结论卡、依据弹层、材料与渠道、提交复核、申诉、问答入口、导出；4 个快捷入口 | 免登录可看，操作经 Cookie |
| `triage.html` | 学生 | 独立初判页：情况填写、结论、依据条、材料清单 | Cookie |
| `qa.html` | 学生 | 政策咨询工作台：会话列表、连续追问、同版原文、办理依据面板、导出、带入初判 | **需登录** |
| `progress.html` | 学生 | 办理进度：状态、复核/申诉、材料包、对外报送 | Cookie |
| `game.html` | 学生 | 政策闯关：6 关情景判断、七态状态机、信任值 3 点、计分、原文卡 | 免登录 |
| `login.html` | 公共 | 注册、登录、重置密码、改密 | 公共 |
| `review.html` | 教师 | 待复核、批量初筛、两级看板、预警、申诉裁决 | 需登录 + 角色 |
| `admin.html` | 管理 | 政策核文、文档入库、发布回滚、账号权限、导出审批、报表 | 需登录 + 角色 |

- 样式：`style.css`（四端共用，主色偏政务暖色，三档结论配色）、`qa-workbench.css`、`game.css`；有 `@media` 响应式。
- 脚本：`qa-workbench.js`（咨询交互）、`game.js`（七态状态机 + localStorage 最高分）。
- 视觉资产：`frontend/assets/` 26 个 SVG（吉祥物"小暖"、政策层级标记、图标）。

### 板块 7 · 验证与运维

| 脚本 | 作用 | 账本口径 |
|---|---|---|
| `tests/smoke.py` | 接口冒烟，tempdir 库副本、8021 端口 | PASS 66/66 |
| `tests/auth_flow.py` | 账号闭环（注册→审核→登录→重置→改密） | PASS 17/17 |
| `tests/ui_flow.py` | 页面与上传链路 | PASS 13/13 |
| `tests/version_flow.py` | 版本发布/回滚内容切换 | PASS 10/10 |
| `tests/game_flow.py` | 闯关：防泄题、六关判定、异常入参、页面入口 | PASS 25/25 |
| `tests/qa_workbench_flow.py` | 咨询工作台端到端 | PASS 11/11 |
| `tests/test_qa_workbench.py` | 工作台单元与契约 | PASS 19/19 |
| `backend/scripts/backup.py` / `restore.py` | 备份两库 + SHA256 清单 / 恢复前自动存快照 | 已执行 |
| `backend/scripts/init_db.py` | 幂等初始化（演示账号 + 16 材料） | 已执行 |
| `backend/scripts/privacy_check.py` | 扫描审计/学生字段明文残留 | 已执行 |
| `docs/verification.md` | 事实台账，558 行；PASS 85 处 / NOT_RUN 44 处 / BLOCKED 5 处 | 持续更新 |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 时间窗 |
|---|------|--------|--------|
| 1 | 对 6 关题面逐条做政策措辞签认（当前 NOT_RUN，防误解） | 资助中心老师 | 上线闯关前 |
| 2 | 真机竖屏 + ≥5 名学生试玩 + 无障碍复核 | 你 / 学生 | 推广前 |
| 3 | 若需公网运行 v0.8.0：另行构建镜像并部署核对（本轮未部署） | 你 | 决定发布时 |
| 4 | 需要一份可视化架构图时，可基于本报告"7 板块"重制 | 我 | 按需 |

---

## ⚠️ 待确认 / 假设 / Non-goals

- **待确认**：本轮未做用户研究、竞品分析与路线图评审——你的请求是"审查代码 + 说明现状"，这三项与请求无关，未强行套用专家团的其他 SOP。
- **边界**：政策 16 份 `pending_verify`；真实在线端点 50 题回归、越界措辞抽样、时延成本、人工语义签认、学生实操均 NOT_RUN；演示视频 NOT_RUN。
- **Non-goals**：不改任何源代码、不发布部署、不清理 I-6 字样、不补推 `v0.6.0-local`、不创建 GitHub Release。
- **合规**：全文不含任何赛事相关字样，符合 I-6 参赛合规约束。

---

## 📚 数据来源 & 成员产出索引

- 五路只读审查：后端核心架构 / 判等工具链 / 咨询问答 / 前端 / 数据与部署测试——产出为各板块的路径:行号事实清单。
- 主理人交叉核实：`main.py` 全路由原文、`Dockerfile` 依赖、`import` 统计、`qa_session` 落库代码、`agents.py` 六代理原文、`provider.py` 三态原文、两个库表结构与行数只读查询。
- 台账：`docs/verification.md`、`VERSION.md`（v0.8.0）、`CONTEXT.md`（术语）。

> 本报告由产品战略团队 AI 协作生成，重要决策请由产品负责人审定。

---

## 复核更正（2026-11-21 晚）

本报告正文是 13:51 的只读快照；此后工作区又有未提交改动，以下两处口径需按当前代码更新：

1. **Provider 在线失败语义**：正文 1.2 与板块 4 写的“失败重试 1 次、仍失败回退离线”只适用于旧兼容问答 `/api/qa`。咨询工作台 `compose_workbench()` 现已改为：在线模式必须拿到通过事实守恒校验的真实模型输出；网络/服务错误可重试 1 次，鉴权/限流/校验失败不重试；任何失败返回 `state=failed`、明确原因码和中文提示，**不回退成看似成功的模板答案**。`/api/provider` 的 `mode_note` 已同步，不再写“失败回退离线”。
2. **原始提问落库**：正文数据底座记录的“原始个人叙述不落库”已被产品决策覆盖。`qa_turn.raw_question` 已落库（旧库自动补列），支持新增、修改、刷新恢复；隐私影响记入 `docs/adr/0002-fact-grounded-language.md`。正文“`qa_session`/`qa_turn` 当前 0 行”为该时点快照，之后预览使用会产生新行。

本更正只对齐事实口径，不改变原报告的审查结论；所有业务改动仍未提交、未推送。
