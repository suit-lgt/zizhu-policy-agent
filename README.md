# 资助政策智能体

状态：骨架与知识底座＋判等 CLI 已落地；A—G 七域最小闭环（三端页面＋接口＋落库＋审计＋备份脚本）已在本机跑通，65 项接口冒烟全过；3 份核心政策已核文并发布版本；Docker 镜像已构建、容器一键起并 `healthy`；C 审核端（批量初筛／两级看板／预警／报送 CSV）与 D 管理端（账号启停／导出审批／报表）已补齐；E 三态 Provider 默认离线＋50 题评测集、G 日志脱敏与统一身份占位已完成（在线模型未接）；演示视频未做；0.5 已补真实账号闭环（注册／管理员审核／重置密码／本人改密，全程文字指引）、6 代理协同 trace、本地上传入库＋核文切块发布门禁，`/login.html` 可用；0.6 已补四端端口（8000 学生／8001 教师／8002 管理／8003 接口）与默认管理员 `admin`；2026-11-07 已部署公网子路径 `https://zhigang.suit-achieve.cn/ZizhuAgent/`，根站不受影响。

目标：把学生与家庭情况翻译成"可申请哪些资助、需要什么材料、去哪里办"，每条结论附政策条款依据，人工复核兜底。
目标用户：家庭经济困难的高职在校学生（首版唯一主用户）。
当前最小闭环：学生勾选家庭与学籍情况 → 规则引擎给出可申请项目清单 → 每项附依据条款与材料清单 → 断网也能跑。

## 代码目录（前后端分层）

```text
backend/                服务端
  app/                  FastAPI 运行包（路由、账号、知识库、问答、多智能体、入库、日志）
  tools/                政策采集、切块、原子化、判等、问答评测、事实核查
  scripts/              初始化、备份、恢复、隐私扫描
frontend/               浏览器端
  login.html            登录／注册
  index.html            学生端
  review.html           教师审核台
  admin.html            管理后台
  style.css             四端共用样式
tests/                  接口、账号、页面、版本回滚联检
data/                   业务库、政策库、上传件、评测集（运行数据，不属于源码）
docs/                   设计、契约、验证与部署文档
Dockerfile              运行镜像：COPY backend + frontend + data
docker-compose.yml      四端端口映射
```

启动命令在项目根执行：`python -X utf8 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`。页面文件只从 `frontend/` 读取，后端不再放 HTML／CSS。

## 文件入口

- `AGENTS.md`：开发规则、七条不变量、阅读路径。
- `docs/project-brief.md`：为什么做、首版范围、成功标准、事实与决定。
- `CONTEXT.md`：政策、条款、原子条件、结论枚举等业务词义。
- `docs/design.md`：主流程、模块职责、技术选择与待验假设。
- `docs/完整功能设计与交付差距.md`：企业交付型的完整功能清单（A—G 七个域）、角色场景、数据实体与状态机、文件级差距矩阵与分期路线。
- `.scratch/qualification-triage/spec.md`：当前功能规格（R-001—R-010、AC-001—AC-008）。
- `.scratch/qualification-triage/issues/01-first-slice.md`：第一个实施任务。
- `docs/ai-contract.md`：模型／程序／人工的责任边界与评测。
- `docs/data-contract.md`：政策条款、原子条件、指标口径与访问规则。
- `docs/eval/qa_eval_report.md`：50 题问答评测报告（自动指标）；`prompts/qa_v1.md`：问答提示词骨架；`docs/开发文档.md`：A—G 七域开发文档（角色权限、数据模型与状态机、接口规范、页面交互、部署运维、测试验收、分期）。
- `docs/verification.md`：验证事实与需求映射；采集／切块／原子化／判等／接口／三端页面／真实库核文发布／Docker 一键起均已 PASS。
- `backend/tools/policy_collector.py`：政策官方文件采集器（命令行 + 固定窗口），产出 `data/staging/`。
- `backend/tools/atomize.py`：原子条件导入／校验／覆盖／导出，产出 `policy_atom` 表与 `data/atoms/atom_index.md`；`regress` 是字段字典改动后的全量回归入口。
- `data/fields.json`：原子条件字段字典 v2（16 个字段，D-009）。
- `data/atoms/core3_atoms.json`：3 份核心文件的 30 条原子条件（R-002 首批）。
- `backend/tools/triage.py`：R-003 确定性判等（CLI），输出三档结论与可回点原文的依据条。
- `data/projects.json`：5 个项目的资格表达式（满足路径／人工确认／展示标准；`projects` 是数组，每项带 `code`）。
- `data/samples/students.json`：3 个合成样例（完整／缺认定／全不满足）。
- `backend/app/main.py`：FastAPI 服务（A—G 接口：核文／发布／回滚／检索、初判／复核／申诉／问答／导出、看板、审计、字典、校级分档）。
- `backend/app/db.py`：业务库 9 表、审计表禁改触发器、演示账号与材料来源种子；库路径支持 `APP_DB` 环境变量。
- `backend/app/auth.py`：`fund_session` Cookie 会话与角色权限、越权 403；页面不再发 `X-Role`／`X-User` 身份头。
- `frontend/index.html`／`review.html`／`admin.html`／`style.css`：学生端、审核工作台、管理后台。
- `backend/scripts/init_db.py`／`backup.py`／`restore.py`：幂等初始化、备份＋SHA256 清单、恢复＋pre-restore 快照。
- `tests/smoke.py`：65 项接口冒烟，在 tempdir 库副本上起独立服务，不写真实库。`tests/auth_flow.py`：17 项账号闭环（注册→审核→登录→重置→改密），端口 8033。`tests/ui_flow.py`：10 项页面与上传链路联检，端口 8035。`tests/version_flow.py`：10 项版本发布／回滚内容切换验证，端口 8036。`backend/tools/fact_support.py`：当前版本原子事实支持机器核验并导出人工复核 CSV。`docs/部署与端口设计.md`：四端端口与上线检查。
- `Dockerfile`／`docker-compose.yml`／`.env.example`：容器一键起（已实测构建与启动，容器 `healthy`）。
- `data/app.db`：业务库（任务、复核事件、申诉、审计、核文、版本、材料、分档；不存姓名）。

## 运行环境

平台与版本：Windows；采集器只用标准库（实测 Anaconda 自带 Python 与 3.13 均可）。
依赖安装方式：无第三方依赖；GUI 需带 Tcl 的解释器。业务服务需 `fastapi` 与 `uvicorn`（已装入 `D:\Sjq\anaconda_install`，实测 fastapi 0.162.0 / uvicorn 0.54.0；装后 pip 提示 `jupyter-server 1.23.4` 要求 anyio&lt;4，与本服务无关，只影响 jupyter）。
配置项名称与用途：白名单域名在 `backend/tools/policy_collector.py` 的 `ALLOWED_HOSTS`；暂存库 `data/staging/policies.db`；原文快照 `data/staging/raw/`。不含任何密钥。
本地样例或测试数据：政策原文取自官方栏目页；学生情况一律使用合成样例，不得用真实学生信息。

容器环境备注（2026-11-07 排障后）：Docker Desktop 4.89.0，引擎 29.7.2；`%LOCALAPPDATA%\Docker\wsl` 曾是指向 `D:\DockerData\wsl` 的坏符号链接（目标目录不存在）导致 `WSL_E_DISTRO_NOT_FOUND`，已改为真实目录，旧链接留档在 `Docker\wsl_link_old_20261107`；数据盘 `docker_data.vhdx` 复用自 `Docker\wsl.bak_c盘`。

## 已验证命令

| 动作 | 命令 | 执行目录 | 前置条件 | 最近验证结果 |
|---|---|---|---|---|
| 采集（整站批量） | `python -X utf8 backend\tools\policy_collector.py crawl --preset 国家 --limit 2` | 项目根 | 联网 | PASS：6 个兄弟栏目共 19 份入库（2026-11-06） |
| 增量差集（重复运行） | 再跑同一条批量命令 | 项目根 | 已有暂存数据 | PASS：已采 URL 直接跳过，不重复请求详情页；第 2/3 轮分别跳过 8／14 条 |
| 采集（指定栏目+关键词） | `... crawl --list <栏目页1> <栏目页2> --include 资助` | 项目根 | 联网 | PASS：省级栏目按关键词只留资助件 |
| 条款切块 | `python -X utf8 backend\tools\policy_collector.py split` | 项目根 | 已有政策正文 | PASS：19 份 → 342 条条款 |
| 条款偏移校验 | `python -X utf8 backend\tools\policy_collector.py verify-clauses` | 项目根 | 已切块 | PASS：342/342 能精确切回原文 |
| 原子条件导入 | `python -X utf8 backend\tools\atomize.py import --file data/atoms/core3_atoms.json` | 项目根 | 已切块、字段字典存在 | PASS：30 条导入，0 报错，4 条旧标准标 superseded |
| 原子条件校验 | `python -X utf8 backend\tools\atomize.py verify` | 项目根 | 已导入 | PASS：30/30 片段能回点原文 |
| 原子覆盖自查 | `python -X utf8 backend\tools\atomize.py coverage` | 项目根 | 已导入 | PASS：3 份文件真漏抽 0，范围外 28 条 |
| 字段字典变更回归 | `python -X utf8 backend\tools\atomize.py regress --file data/atoms/core3_atoms.json` | 项目根 | 已切块 | PASS：30 条导入、30/30 校验、真漏抽 0、无原子字段列出 stage／income_band／school_tier |
| 原子清单导出 | `python -X utf8 backend\tools\atomize.py export` | 项目根 | 已导入 | PASS：`data/atoms/atom_index.md` |
| 确定性判等自测 | `python -X utf8 backend\tools\triage.py --selftest` | 项目根 | 已切块＋原子已导入 | PASS：12/12（AC-001／002／003＋I-1／I-2／D-010） |
| 单例判等 | `python -X utf8 backend\tools\triage.py --input data\samples\students.json --case complete --pretty` | 项目根 | 同上 | PASS：5 个项目三档结论，每项带条款与原文偏移 |
| 启动业务服务 | `D:\Sjq\anaconda_install\python.exe -X utf8 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8010` | 项目根 | fastapi／uvicorn 已装 | PASS：`/api/health` ok、页面 200、判等 200（2026-11-06） |
| 业务库初始化 | `python -X utf8 backend\scripts\init_db.py` | 项目根 | 可选 `APP_DB` | PASS：4 用户／16 材料／0 分档／9 任务；政策库 19 份／342 条（2026-11-06） |
| 三端页面 | `/`、`/review.html`、`/admin.html` | 服务 8010／8011 | 服务已起 | PASS：三页 200；截图 `student_page.png`／`review_page.png`／`admin_page.png` |
| 接口冒烟 | `python -X utf8 tests\smoke.py` | 项目根 | fastapi／uvicorn | PASS：**65/65，exit 0**；tempdir 库副本＋8021 端口，不写真实库 |
| 备份 | `python -X utf8 backend\scripts\backup.py` | 项目根 | 两库存在 | PASS：`backups/20261107-011327`（2 文件＋SHA256 manifest） |
| 恢复 | `python -X utf8 backend\scripts\restore.py --from backups\<name>` | 项目根 | 有备份 | NOT_RUN：会覆盖活动库，未在运行态执行（恢复前自动存 pre-restore 快照） |
| 容器编排校验 | `docker compose config` | 项目根 | Docker CLI | PASS：`name: fund-agent`、8000–8003 四端映射到容器 8000、挂载 `./data`、healthcheck 完整；端口说明见 `docs/部署与端口设计.md` |
| 容器一键起 | `docker compose build` → `docker compose up -d` | 项目根 | Docker 守护进程 | PASS：镜像 `fund-agent:0.5`，容器 20 秒内 `Up (healthy)`，`/api/health` 显示 3 份已核＋版本 `pv-20261107T041009` |
| 演示页（含结果） | `http://127.0.0.1:8010/?case=complete` | 浏览器 | 服务已起 | PASS：截图 `docs/demo/triage_page.png` |
| 导出核对清单 | `... export` / `... export-clauses` | 项目根 | 已有数据 | PASS：`policy_index.md`、`clause_index.md` |
| 固定窗口采集 | `D:\Sjq\anaconda_install\python.exe -X utf8 backend\tools\policy_collector.py gui` | 项目根 | 需 Tcl 支持 | 窗口构建 PASS；点击采集未人工实操 |
| 业务系统启动 | 见上「启动业务服务」 | 项目根 | fastapi／uvicorn | PASS（本机 8010）；容器方式已 PASS（8000） |
| 真实库核文＋发布 | 管理后台「政策核文」→「版本与回滚」 | 服务已起 | admin 身份 | PASS：3 份核心已核，发布 `pv-20261107T041009`（docs=3／clauses=93／atoms=26） |
| 问答评测 50 题 | `python -X utf8 backend\tools\qa_eval.py --report docs\eval\qa_eval_report.md` | 项目根 | 政策库已切块 | PASS：50/50（命中 35/35、边界 10/10、拒答 5/5），引用 115 条 0 偏移错误，P95 24.9 ms |
| 三态 Provider 自测 | `python -X utf8 -m backend.app.provider --selftest` | 项目根 | 无 | PASS：5/5，含在线缺配置回退离线 |
| 日志脱敏自测 | `python -X utf8 -m backend.app.logs --selftest` | 项目根 | 无 | PASS：6/6（手机号／身份证／银行卡／邮箱／姓名键／嵌套） |
| 隐私自查 | `python -X utf8 backend\scripts\privacy_check.py` | 项目根 | 业务库存在 | PASS：audit_log 12 行＋triage_task 11 行无明文 |
| 身份占位 | `GET /api/auth/whoami` | 服务已起 | — | PASS：`mode=dev`、非法角色 403；`AUTH_MODE=cas` 未配置 501 |

## 使用与限制

主要操作：学生勾选学籍与家庭情况并勾选隐私授权 → 得到可申请／需核实／暂不适用清单与依据条款 → 提交复核 → 审核台通过或退回 → 学生申诉 → 管理员裁决（可改判）→ 导出材料包；管理员可核文、发布版本、回滚、查审计、维护校级分档。

已知限制：19 份政策中 **3 份核心已核文并发布**（财教〔2021〕310号 2021-12-30、财教〔2024〕181号 2024-10-25、粤财规〔2026〕1号 2026-03-01；版本 `pv-20261107T041009`），其余 16 份仍 `pending_verify`，全局 `policy_verified=false`；家庭经济困难认定办法（粤教助〔2023〕2号）与校级分档细则未入库；材料清单 16 条来源标“学校要求”、无条款来源；统一身份仍是演示态 `X-Role`／`X-User`（已加 CAS 占位，`AUTH_MODE=cas` 未配置即 501）；问答为条款关键词检索＋拒答，非语义 RAG；三态 Provider 已实现但默认离线、未接真实模型；50 题评测集已建并自动 50/50 通过，人工事实核验未做；镜像 `fund-agent:0.5` 已构建并 healthy；0.5 已补账号闭环、多智能体 trace、本地上传入库；16 份历史草稿仍未核文且仍可被检索；演示视频未拍。

交付状态：A—G 最小闭环可运行并有页面截图、65 项冒烟与 17 项账号闭环；3 份核心政策已核文发布；容器一键起已实测 `healthy`；未验收；未做学校部署；未实际使用；演示视频未做；0.5 已补真实账号闭环（注册／管理员审核／重置密码／本人改密，全程文字指引）、6 代理协同 trace、本地上传入库＋核文切块发布门禁，`/login.html` 可用；0.6 已补四端端口与默认管理员。
