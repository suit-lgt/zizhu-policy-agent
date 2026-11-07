# 验证记录与需求映射

初始化状态：NOT_RUN。文件存在、脚本成功、测试数量均不代表业务验收通过。

## 验证环境

版本／提交／产物标识：无（业务代码未开始）
日期、执行者：2026-11-06 / 梁广通
环境与数据：Windows 本机；Python 3.13.16；无测试数据库；学生情况样例全部为合成数据
模型与模式：无（未接入任何模型）
隔离措施：当前不存在业务数据，尚未建立隔离测试库；首次写测试时必须先建独立测试库

## 映射与结果

| 需求 | 验收条件 | 任务票 | 实现位置 | 验证方法／命令 | 结果 | 证据路径 | 限制 |
|---|---|---|---|---|---|---|---|
| R-001 政策入库与版本发布 | 版本快照、回滚、文号校验 | 待建 | 待实现 | 导入 3 项政策后比对条款数 | NOT_RUN | 无 | 政策原文未取得 |
| R-002 条件原子化 | 每项 ≥3 个原子条件且映射字段 | 01-first-slice | 3 份核心文件已原子化（30 条），其余 16 份未抽 | 人工核对 + 字段覆盖率 | PASS（首批 3 份） | data/atoms/atom_index.md | 字段字典 v2（D-009）；认定办法未入库 |
| R-003 确定性判等 | AC-001／002／003 | 01-first-slice | 已实现（`tools/triage.py`，CLI 级，未接 API） | `triage --selftest` 12 项断言 | PASS：AC-001 命中 4 项／AC-002 need_check 并指明缺项／AC-003 无命中给渠道，12/12 通过 | data/projects.json、data/samples/students.json | 未接 FastAPI、未做前端；政策文号未核，结论仅供内部验证 |
| R-004 依据条与材料清单 | 每条结论 ≥1 条款，可回点原文 | 01-first-slice | 依据条已实现（每条结论挂 clause_id + 原文 span）；材料清单为通用清单，无条款来源 | 条款覆盖率校验 + 抽检 | PARTIAL | triage 输出 reasons 字段 | 材料清单不能标成"政策要求"，须注明通用 |
| R-005 政策问答 | 有引用、无依据拒答 | 待建 | 待实现 | 20 题人工核验 | NOT_RUN | 无 | 评测集未建 |
| R-006 语音入口 | 识别失败回退 | 待建 | 待实现 | 人工操作检查 | NOT_RUN | 无 | 二期，可降级 |
| R-007 复核与申诉 | AC-007／008 | 待建 | 待实现 | 状态 + 权限测试 | NOT_RUN | 无 | — |
| R-008 批量工作台 | 名单导出 | 待建 | 待实现 | 功能检查 | NOT_RUN | 无 | 二期 |
| R-009 看板 | 口径标注、事件计数 | 待建 | 待实现 | 数据核对 | NOT_RUN | 无 | 二期 |
| R-010 导出 | AI 声明与版本号 | 01-first-slice | 待实现 | 导出件检查 | NOT_RUN | 无 | — |
| 降级 | AC-005 断网全链路 | 01-first-slice | 待实现 | 断网演练 | NOT_RUN | 无 | — |
| R-001 采集前置：官方文件可读 | 栏目页可读、字段可抽 | tools/policy_collector.py | 已实现 | `crawl` 国家/省级两栏 | PASS（2026-11-06，4 条） | data/staging/policy_index.md | 只证明可采与字段抽取，未核对政策口径 |
| R-001 采集：多栏目批量与增量 | 6 个兄弟栏目一次走完；重复运行不重采 | tools/policy_collector.py | 已实现 | `crawl --preset 国家 --limit 2` 连跑 3 轮 | PASS：累计 19 份；第 2/3 轮分别跳过 8／14 条旧条目 | data/staging/policies.db（表 crawl_run 18 条） | 只证明可采与增量判定，未核文号 |
| R-002 条款切块（原子化前置） | 每份文件切成条款且可回点原文 | tools/policy_collector.py | 已实现 | `split` + `verify-clauses` | PASS：19 份 → 342 条，342/342 偏移可切回原文 | data/staging/clause_index.md | 切块≠原子条件；字段映射未做 |
| R-002 原子条件导入 | 字段取值合法、原文片段逐字可定位 | tools/atomize.py | 已实现 | `atomize import --file data/atoms/core3_atoms.json` 再 `verify` | PASS：30 条导入，30/30 定位校验通过，0 报错 | data/atoms/core3_atoms.json、data/atoms/atom_index.md | 首次导入曾因 DEF-01 用"依据"取值校验被拦，已给定义类原子开豁免 |
| R-002 原子覆盖自查 | 有资格关键词的条款不漏抽 | tools/atomize.py | 已实现 | `atomize coverage` | PASS：3 份文件真漏抽 0，范围外 28 条（职责/预算/学前/中职/程序） | 同上 | 范围判定靠关键词，非人工逐条确认 |
| R-002 标准版本优先 | 新旧口径冲突时旧值标 superseded | tools/atomize.py | 已实现 | `atomize import` 后查 `superseded` | PASS：4 条被替代（310号 3300；181号 国奖/励志被 2026 省级吸收）；同版本多条标准不互相替代 | policy_atom 表 | 仅按日期取最新，未核文号真伪 |
| R-002 字段字典扩展回归 | v2 增字段后 30 条原子仍可导、可定位、无漏抽 | tools/atomize.py | 已实现 | `atomize regress --file data/atoms/core3_atoms.json` | PASS：导入 30、校验 30/30、真漏抽 0、无原子字段列出 `stage`／`income_band`／`school_tier` | data/fields.json、data/atoms/atom_index.md | 回归是机器校验，不证明档界与档位映射正确 |

## 关键检查

- 正常路径、边界、权限、重复请求与恢复：NOT_RUN（无实现）
- 必要构建／文件打开／界面操作：NOT_RUN
- 真实样例与用户任务：NOT_RUN（仅有合成样例设计，未实操）
- AI 事实支持、缺失信息、模型失败：NOT_RUN（未接入模型）
- 实际收益：未测。初判一致率、完成耗时、材料清单可实施率均无基线，禁止写目标值为实测值。

模板包自身的结构自测（与本项目业务验收无关，仅记录可用性）：`scripts/test_template_tools.py` 实跑 8 项通过，19.711 秒；该结果只证明模板结构有效，不证明本项目任何功能。

政策采集实测（2026-11-06，逐项核对输出）：
- 国家栏目（全国学生资助管理中心 高校学生资助）3 条：文号为 财教〔2025〕69号、财教〔2024〕188号、财教〔2024〕181号，发布日 2025-03-31 / 2024-11-06 / 2024-10-29，正文 461—1913 字，尾部收在落款日期，无页脚残留。
- 省级栏目（广东省教育厅，关键词"资助"）1 条：粤财规〔2026〕1号，自动识别施行日期 2026-03-01，正文 7510 字。
- robots.txt：两站均无（HTTP 404），按未声明限制处理，间隔固定 3 秒、单线程。
- 去重：重复采集同一栏目返回 `duplicate`，不新增行。
- 已知偏差：文号只在页首 60 字或标题内认定；正文深处的文号视为"引用他件"不采用（此项由一条误判复现后修正）。施行日期多数未在正文声明，保持 pending_verify。
- GUI：窗口可构建（Anaconda 解释器）；点击采集未做人工实操，标未验证。

多栏目批量与增量（2026-11-06，同一命令连跑 3 轮 `crawl --preset 国家 --limit 2`）：
- 覆盖 6 个栏目：综合政策、高校学生资助、中等职业教育、普通高中、义务教育、学前教育；累计入库 19 份，文号全部抽出（财教、教财、教财厅函、国办发、中发、退役军人部发等 6 类字号）。
- 增量：第 1/2/3 轮分别 skipped 3／8／14 条，已采 URL 不再请求详情页；每轮各栏新增数量稳定等于 `--limit`，证明"重复条目与附件页不占新增额度"。
- 跨栏目重复：同一文件在多个栏目挂载时按内容哈希判重（3 轮累计 duplicate 10 条），不产生重复记录。
- 非政策正文：附件下载页（如《家庭经济困难学生认定申请表（样表）》76 字）识别为"非政策正文"跳过，不计失败。
- 运行留痕：`crawl_run` 每条记录栏目、候选数、新增/更新/跳过/失败，可回答"这批文件是哪次、哪个栏目来的"。
- 偏差标注：财教〔2024〕64号 列表日期 2025-10-10 与落款 2024-04-10 相差 548 天，已在 `note` 标"发文日期需核对"。这类落差是栏目页挂载日期，不是发文日期。

条款切块（2026-11-06）：
- 19 份切成 342 条：有"第X章/第X条"结构的按条切（含章标题作为独立记录），通知类按自然段切（"一、二、三"各自成条）。
- 每条记录带 `char_start/char_end`，`verify-clauses` 逐条比对 `body_text[start:end] == 条款正文`，342/342 通过——这是"引用可回点原文位置"（I-3）的硬校验。
- 切块 ≠ 原子化：条件字段（家庭人均收入、户籍、学籍状态等）尚未从条款中抽取，U-002 未解。
- 学段说明：19 份含学前、义务教育、普通高中、中职、高校各学段文件；高职学生主闭环只用其中与高校学生资助相关的部分，学段适用性须在原子化时逐条标注，不能整库混用。

原子条件抽取（2026-11-06，3 份核心文件）：
- 范围：财教〔2021〕310号（国家层面资金办法与旧标准）、财教〔2024〕181号（2024 年标准调整）、粤财规〔2026〕1号（广东现行实施办法，2026-03-01 施行）。
- 结果：30 条原子，覆盖 6 个项目——本专科生国家助学金 10 条、国家励志奖学金 5 条、国家奖学金 5 条、广东省家庭经济困难大学新生资助 5 条、校内学费减免 4 条、困难认定口径 1 条。
- 校验：`atomize import` 五重校验（字段在字典、取值合法、`op` 合法、`quote` 在条款内定位、偏移逐字一致）全部通过，`verify` 复核 30/30；首次导入曾被 DEF-01 拦下（"依据"类取值不在 `income_difficulty` 枚举），已改为定义类原子豁免枚举校验。
- 判等边界（按 `role`／`determinable` 统计）：可自动判等 12 条（学段／学籍／学制／年级／身份类）；`需认定` 5 条（依赖学校困难认定结论）；`评审` 2 条（特别优秀、品学兼优）只提示需评审；`标准` 9 条只展示金额（含 GZ-10 的 2500—5000 区间）；`排除` 1 条（XS-04 原文含"原则上"）标需人工，不自动判不通过；`定义` 1 条（DEF-01）只指认认定依据文件。
- 收入分档（字段字典 v2）：政策只给"平均 3700、区间 2500—5000、可分 2—3 档"（GZ-06、GZ-10），不给收入档界、也不给"哪一档"的映射。`income_band` 与 `school_tier` 因此无原子支撑（`regress` 已列出），档位只能输出"区间参考＋需核实"；补校级分档细则或粤教助〔2023〕2号后才可能变确定值。
- 版本冲突：财教〔2021〕310号 的助学金 3300 元、财教〔2024〕181号 的国奖 10000／励志 6000（已被 2026 省级办法吸收）共 4 条标 `superseded=1`，原文保留可回溯；判等只用未替代项。
- 覆盖自查：3 份文件合计 80 条条款，挂原子的 7 条，其余 28 条被判为"本范围外"（职责分工、预算分配、学前／中职／高中、程序性规定），真漏抽 0。
- 已知缺口：粤教助〔2023〕2号（家庭经济困难学生认定办法）未入库，DEF-01 只能指认"按该文件认定"，具体认定条件不可判；校内学费减免的校级细则（比例、额度）无来源。

## 遗留问题

| 问题 | 影响 | 状态 | 处理人／下一步 | 是否阻碍交付及理由 |
|---|---|---|---|---|
| 3 项试点政策原文未取得（U-001） | 无法原子化，首票无法开工 | 部分推进：已采 19 份、342 条条款、30 条原子；仍缺粤教助〔2023〕2号与校级细则 | 向学校资助中心索取现行文件 | 阻碍：认定条件无原文即不可判 |
| 原子条件字段字典未定（U-002） | 表单与判等逻辑无法定型 | 已落 v2（16 字段，D-009）：增 `income_band`／`school_tier`，30 条原子回归通过 | 拿到校级分档细则后补"收入档→档位"映射 | 不阻碍：有默认值可推进 |
| 政策文号／生效日期全部未核（M-001） | 结论不可对外，不能进答辩材料 | 部分推进：已采 19 份原文、342 条条款，文号待人工确认 | 按 `policy_index.md` 逐条对照官方页面与文件原件 | 阻碍对外展示，不阻碍内部开发 |
| 50 条评测集未建（U-003） | 一致率目标无法验证 | 未处理 | 12 月前凑齐含边界与拒答案例 | 不阻碍首票，阻碍验收结论 |
| 技术栈未压测（H3） | 2C4G 可能承载不住 | 未处理 | 一次真实规模压测 | 不阻碍，但影响部署方案 |

确定性判等（2026-11-06，`tools/triage.py` CLI 级）：
- 规则来源：`data/projects.json` 中 5 个项目的满足路径（GZ 两条路径：普通困难生／退役士兵；LZ、GJ 各一条；XS 一条＋1 条人工排除；XN 三条身份路径），只加载 `superseded=0` 的 26 条现行原子（4 条旧标准被排除）。
- 自测 12 项全过：AC-001 完整信息命中 4 项（GZ 可申请、XN 可申请、LZ 需核实、GJ 需核实），每项 ≥1 条款、材料非空；AC-002 缺困难认定 → GZ 需核实且 `missing_fields=[income_difficulty]`；AC-003 全部不满足 → 命中 0 并给出学校资助中心渠道。
- 口径约束实测：评审类（学术表现）恒为需核实且 `review_required=true`，不因学生自填"一般"而判不通过；`income_difficulty=未认定` 判暂不适用并在依据中说明"尚未取得认定结论"，`待认定` 则需核实；XS-04 原文含"原则上"，仅在学生已获省财政其他专项时才降级并提示人工确认，不作为排除依据。
- 引用形态：每条 reason 带 `clause_id`／`doc_no`／`article_no`／`quote`／`char_start-char_end`，可直接回点原文（I-3）；同一 clause 内被包含的短片段自动去重，避免同一句话重复刷屏。
- 未做：FastAPI 接口、前端单页、任务落库与复核状态机；材料清单为通用清单，非条款抽取。

接口与单页前端（2026-11-06，`app/main.py` + `app/static/index.html`，本机 127.0.0.1:8010 实跑）：
- `POST /api/triage`：完整样例返回 200，`hit_count=4`（GZ 可申请／LZ 需核实／GJ 需核实／XS 暂不适用／XN 可申请）；响应与落库均剔除姓名（I-4）。
- 参数校验：`{"grade":99}` 返回 422 并提示「整数取值超出范围（允许 1–8）」，不做静默修正。
- `GET /api/triage/{task_id}` 返回同结果；`triage_task` 表累计 3 行，`created_at`／`policy_version`／`policy_verified` 一并落库（D-005 版本锚定）。
- `GET /api/clause/351` 返回第十一条全文 1088 字、正文偏移 2353–2393、官方页面 URL 与 `pending_verify` 状态，前端据此弹层回点原文（I-3）。
- `GET /api/export/{task_id}` 导出 Markdown 3258 字节：结论表＋依据原文＋材料清单＋办理渠道＋AI 声明与版本号。
- `GET /` 页面 200；截图 `docs/demo/triage_page.png`（含表单与 5 张结论卡，可申请／需核实／暂不适用三色区隔）。
- 未做：Docker 一键起（本机 Docker Desktop 已安装但守护进程未运行，`docker version` 连不上 npipe）、离线模式、复核状态机、与真实身份系统对接。

## 本次结论

已证明：项目骨架、需求编号、验收条件与不变量已成型；政策采集（多栏目批量、增量差集）、条款切块、原子条件抽取与确定性判等链路可运行（CLI 级）；342 条条款均能回点原文，30 条原子均能在条款内逐字定位，三档结论在 3 个合成样例上 12/12 断言通过，并经 FastAPI 接口与单页前端跑通（判等、回点原文、任务落库、Markdown 导出）。
未证明：Docker 一键起与断网演示；材料清单有政策条款来源；任何政策口径正确（文号未核，`policy_verified=false`）；任何指标达标；认定办法原文与校级分档细则未入库。
可交付范围：文档骨架与首版规格、政策采集／切块／原子化／判等工具、单页演示服务（本机运行）；不含 Docker 镜像与演示视频。

## v0.2 按《开发文档》执行（2026-11-06 晚，实测）

范围：A—G 七域的最小闭环落到可运行代码；业务库 `data/app.db` 与政策库 `data/staging/policies.db` 分离；三端页面（学生／审核／管理）可点。

已实测 PASS：

- 初始化 `scripts/init_db.py` exit 0：`app_user` 4、`material_item` 16（`source_type=学校要求`）、`school_tier` 0、`triage_task` 9；政策库 19 份／342 条。
- 接口冒烟 `tests/smoke.py`：**36 项全过，exit 0**。脚本把两库复制到 tempdir，用 `APP_DB`／`POLICY_DB` 指向副本并另起 8021 端口，不写真实库。覆盖：健康检查；未勾选授权 400；初判 200 且 `hit_count=4`；响应不含姓名；`grade=99`→422 且提示 1–8；提交复核；退回缺理由 422；通过；申诉；管理员裁决改判 → `amended`；看板；政策清单 19 份；未核文发布 400；核文 200；发布生成 `pv-*` 版本；版本列表 `current`；回滚 200；问答命中 3 条；无依据问题拒答；条款检索；材料来源类型非空；分档未配置说明；新增分档；审计留痕（`triage`／`submit_review`／`denied`／`policy_verify`／`policy_publish`／`policy_rollback`）；学生查审计 403；导出含“依据原文”与“仅供参考”；三页 200；审计表 `UPDATE` 被触发器拒绝。
- 页面截图（本机实跑实例）：`docs/demo/student_page.png`（1280×3600）、`docs/demo/review_page.png`、`docs/demo/admin_page.png`。
- 容器编排静态校验：`docker compose config` exit 0（`api` 服务、8000:8000、挂载 `./data`、healthcheck 完整）。
- 演示数据：两条示例任务已提交复核，审核台待办 2 条；看板 7 `triaged`／2 `reviewing`。

本轮修掉的真实缺陷：

1. `app/db.py` 的种子按 dict 遍历 `projects`，而 `data/projects.json` 的 `projects` 是**数组** → `AttributeError`；改为按 `p["code"]` 迭代后才建出 16 条材料。
2. `tests/smoke.py` 把中文 query 直接拼进 URL，`urllib` 抛 `UnicodeEncodeError`（第 25 项后中断）→ 在 `call()` 里对 path 做 `quote` 并保留 `?q=` 结构，复跑 36/36。
3. `docker-compose.yml` 缺 `name:`，在中文目录下 compose 报 `project name must not be empty` → 补 `name: fund-agent` 后 `config` 通过。
4. 审核台时间显示 UTC 原文（`2026-11-06T15:05:25+00:00`）→ 三页统一格式化为 Asia/Shanghai 本地时间。

NOT_RUN／已知未做：

- `docker compose up --build`：本机 Docker Desktop 守护进程未启动（`docker version` 连不上 `npipe:////./pipe/dockerDesktopLinuxEngine`），镜像构建与容器内启动均未验证；只完成编排静态校验。
- 真实库上的核文／发布：冒烟在临时副本上完成；`data/staging/policies.db` 的 19 份仍全部 `pending_verify`、`publishable=0`。
- 统一身份：仍为 `X-Role`／`X-User` 演示态，未接学校 CAS／OAuth。
- 校级分档表为空，收入档→档位仍只能“区间参考＋需核实”。
- 材料清单 16 条 `source_clause_id` 全为 NULL，来源标“学校要求”而非条款。
- 演示视频未拍；50 题评测集未建。
- 端口 8010 上 23:05 启动的旧实例，`/api/board` 返回旧快照（9 `triaged`），同进程 `/api/tasks` 已是 7／2；已把看板改为逐行统计并新增 `db_path` 自证，新实例 8011 返回 7／2 正确。**正式端口需重启后复验**，重启动作未执行（等待人工确认）。

## v0.2 结论

已证明：A 核文／发布／回滚／检索，B 初判／依据／材料／问答／复核／申诉／导出／隐私授权，C 待办／看板／申诉裁决，D 审计／字典／校级分档，E 问答检索与拒答，F 初始化／备份恢复脚本／健康检查／容器编排，G 越权 403 与审计禁改——均在本机跑通，并由 36 项冒烟守住。

未证明：容器一键起真实可用；任何政策口径正确（文号未核，`policy_verified=false`）；真实学生数据下的复核申诉闭环；Docker 镜像与演示视频交付。

## v0.2.1 真实库核文与发布（2026-11-07 实测）

在本机真实库（`data/app.db`＋`data/staging/policies.db`）上完成 3 份核心文件的核文与发布。核文依据一律取自**已采集的官方原文**，不凭记忆补写：

| doc_id | 文号 | 发布机关 | 生效日期 | 核对依据（原文） |
|---|---|---|---|---|
| 32 | 财教〔2021〕310号 | 财政部、教育部、人力资源社会保障部、退役军人部、中央军委国防动员部 | 2021-12-30 | 落款“2021年12月30日”；第二十九条“本办法自印发之日起施行” |
| 27 | 财教〔2024〕181号 | 财政部、教育部、人力资源社会保障部 | 2024-10-25 | 落款“2024年10月25日”；正文“本通知自印发之日起施行”（页面发布日期 2024-10-29，晚于落款 4 天，取落款） |
| 28 | 粤财规〔2026〕1号 | 广东省财政厅、广东省教育厅、广东省人力资源和社会保障厅、广东省退役军人事务厅、广东省军区动员局 | 2026-03-01 | 落款“2026年1月5日”；第三十八条“自2026年3月1日起施行，有效期5年” |

- 核文接口 `POST /api/policy/{id}/verify` 三次均 200：写 `policy_verify`（verifier、`verified_at`、note 记原文依据），`audit_log` 留 `policy_verify` 三条。
- 发布接口 `POST /api/policy/28/publish` → `pv-20261107T041009`，`docs=3`、`clauses=93`、`atoms=26`，状态 `current`。
- 修掉发布口径 bug：原先 `clauses` 统计全库 342 条，只发布 3 份也会写 342；改为只统计已核／已发布文件的条款（13＋45＋35＝93）。
- 任务版本锚定（D-005）：新初判任务 `policy_version` 取当前发布版本 `pv-20261107T041009`，不再固定写 draft（实测 `task-a6f8c5198866`）。
- 健康检查改读真实核文状态：`policy_docs_verified=3`、`policy_docs_total=19`、`policy_verified=false`（仍有 16 份草稿，全局保守声明不变）。
- 未做：其余 16 份未核（多为中职／高中／学前等非首版范围文件）；`policy_verified=false` 保留，避免把“3 份已核”误读成“全部已核”。

## v0.2.2 Docker 一键起（2026-11-07 实测）

镜像构建与容器启动已在本机真实执行，不再是 NOT_RUN：

| 项 | 命令 | 结果 |
|---|---|---|
| 镜像构建 | `docker compose build` | PASS：`fund-agent:0.2`，`build_exit=0` |
| 一键启动 | `docker compose up -d` | PASS：`Container fund-agent Started`，10 秒内 `Up (healthy)` |
| 端口映射 | — | `0.0.0.0:8000->8000/tcp`（容器内 8000，`${PORT:-8000}`） |
| 健康检查 | `curl /api/health` | `{"ok":true,...,"policy_version":"pv-20261107T041009","policy_docs_verified":3,"policy_docs_total":19,"atoms_current":26}` |
| 页面 | `/`、`/review.html`、`/admin.html` | 均 200 |
| 初判（容器内） | `POST /api/triage` | 200，`hit_count=4`，`policy_version=pv-20261107T041009` |
| 导出 | `GET /api/export/{task_id}` | 200 |
| 资源占用 | `docker stats --no-stream` | CPU 0.17%，内存 39.25 MiB |
| 删掉重起（一键起验收） | `docker compose down` → `docker compose up -d` | PASS：4.6 秒内 `/api/health` 返回 200，远低于“5 分钟内可用”的验收口径 |

容器读的是挂载的 `./data`（`APP_DB=/app/data/app.db`、`POLICY_DB=/app/data/staging/policies.db`），因此容器内看到的核文状态与版本与宿主一致（3 份 verified、`pv-20261107T041009` current）。

启动失败与排障（真实发生，按顺序）：

1. `docker version` 连不上 `npipe`；`docker desktop start` 后台跑了 9 分钟仍 503。
2. `docker desktop diagnose` 的 bootstrap 日志给出根因：`Wsl/Service/WSL_E_DISTRO_NOT_FOUND` —— Docker 的 WSL 发行版 `docker-desktop` 已被注销。
3. 进一步发现 `%LOCALAPPDATA%\Docker\wsl` 是指向 `D:\DockerData\wsl` 的**符号链接**，而目标目录不存在（此前把 Docker 数据挪到 D 盘时没把目录建起来）。补建目录后发行版能被重建（`wsl -l -v` 出现 `docker-desktop`），但仍起不来。
4. 真实错误是 `creating virtual disk: The filename, directory name, or volume label syntax is incorrect` —— Docker 创建 2.7 GB 数据盘 `docker_data.vhdx` 失败，与链接无关。
5. 处理：把 `wsl` 链接改名为真实目录（`Docker\wsl_link_old_20261107` 留档），并把备份里的 `Docker\wsl.bak_c盘\disk\docker_data.vhdx`（2753 MB）复制到 `Docker\wsl\disk\docker_data.vhdx` 让 Docker 复用已有数据盘。再次 `docker desktop start` 后引擎就绪：`docker info` 返回 server **29.7.2 / linux**。

未做／未证明：镜像内 `fastapi==0.115.*`／`uvicorn==0.30.*` 与宿主 0.162.0／0.54.0 不同版本，只在容器内跑通冒烟级接口，未做容器内整套 `tests/smoke.py`；未做 `docker compose down && up` 的反复重建演练；未做断网（离线）容器演练；未做 CI；演示视频未拍。

## v0.3 C 审核端与 D 管理端增量（2026-11-07）

背景：差距口径里 C 的批量初筛／两级看板／预警／报送、D 的账号权限／导出审批／报表都"完全没有"。本轮只补这两块，不动 A/B/E/F/G。

### 变更
- 业务库新增 `export_request` 表（status 默认 pending）；`app_user` 补 `enabled`（默认 1）与 `updated_at`，`init_schema()` 对旧库做补列迁移。
- `app/auth.py`：角色合法后再校验 `app_user.enabled`，停用即 403「账号已停用，请联系管理员」并写 `denied/account` 审计。
- 接口（角色约束见开发文档 §5）：`review/queue`、`review/batch`、`board/levels`、`warnings`、`report/summary`、`report/export.csv`、`users`、`users/{id}/enable`、`export-requests` 三件套；导出 Markdown 在有批准记录时追加审批人与时间。
- 页面：`review.html` 新增预警、两级看板、批量初筛（勾选＋批量通过/退回）；`admin.html` 新增账号权限、导出审批、报送报表三个页签。

### 实测（`tests/smoke.py`，tempdir 库副本，8021 端口）
56 项，通过 56，失败 0，exit 0。新增 18 项覆盖：队列带等待时长；批量退回缺理由 422；批量通过 2 条（count_ok=2）；重复批量 skipped=2；两级看板 5 个项目；预警含 `policy_unverified`（3/19）；报表 200 且带版本；CSV 含表头；辅导员取报表 403；账号列表 4；停用 200、停用后 403、启用 200；唯一管理员 400；导出申请 pending、学生查申请 403、审批 200、导出件含"导出审批：已批准"。

### 容器与页面
- `docker compose up -d --build` 后容器 `Started`；`/review.html`、`/admin.html` 均 200；`/api/users` 返回 4 个账号。
- 截图：`docs/demo/review_batch.png`（1280×1900）、`admin_users.png`、`admin_exports.png`、`admin_report.png`，均 Read 回读确认。
- 期间修掉的缺陷：`admin.html` 三段落点错误曾插进 `#mask` 弹窗内部，已用脚本移回 `.wrap` 内并做 div 配平自检（46/46），页面空白间距随结构修正消失。

未做：报送 PDF 包；导出审批对接真实公文流转；批量初筛分页与按项目筛选；预警阈值配置化（现为 72 小时、退回率 30% 硬编码）。
## v0.4 E AI 能力层与 G 安全合规（2026-11-07）

口径：E 先保持"离线规则模式"做完所有功能，再接真实模型；本轮不改判等、不引模型结论。G 只做日志脱敏与统一身份占位，不做加密与等保材料。

### 变更
- `app/provider.py`：三态 `offline`（默认，不联网）／`online`（OpenAI 兼容，`FUND_LLM_BASE/KEY/MODEL`）／`mock`（自测）；输出过结构＋业务校验（长度、禁用措辞、必须引用本次文号），失败重试 1 次，仍失败回退离线并记 `last_error`；`GET /api/provider` 与 `/api/health` 均可查当前模式。
- `app/qa.py`：问答逻辑从接口层抽出（接口与评测共用一套）；检索由整句退化为 4/3/2 字片段；新增域外提问拦截（火星／天气／论文／股票等），拒答带 `reason=off_domain`。
- `prompts/qa_v1.md`：提示词骨架版本化；每次问答返回 `prompt_version`。
- `data/eval/qa_50.json` + `tools/qa_eval.py`：35 命中／10 边界／5 拒答；自动校验引用回点（clause 存在、偏移与文号一致）、解释区越界措辞、判定语气、耗时；报告写入 `docs/eval/qa_eval_report.md`。
- `app/logs.py`：手机号、身份证、银行卡、邮箱、姓名字段脱敏；`app/db.py` 的 `audit()` 在落库前对 `target`/`detail` 脱敏；`app/main.py` 增加访问日志中间件（只记方法／路径／状态／耗时，查询串脱敏，不记请求体）。
- `scripts/privacy_check.py`：扫描 `audit_log` 与 `triage_task.student_json` 的明文残留。
- `app/identity.py`：`dev`（请求头）／`cas`（未配置返回 501，不做假登录）；`GET /api/auth/whoami`；替换点明确为 `app/auth.py:current_user`。
- 页面/导出件标注模式：学生端问答区显示"回答模式＋提示词版本"，导出 Markdown 头部加"说明模式"行。
- 学生页补"申请导出审批"按钮（此前只有 POST 接口、页面无入口，导出审批页无法通过真实操作产生数据）。
- `docker-compose.yml` 镜像改 `fund-agent:0.4`，透传 `FUND_LLM_*`、`AUTH_MODE`、`CAS_BASE`、`LOG_LEVEL`；`.env.example` 同步。

### 实测
| 项 | 命令/入口 | 结果 |
|---|---|---|
| 问答评测 | `tools/qa_eval.py --report docs/eval/qa_eval_report.md` | PASS 50/50：命中 35/35、边界不判定 10/10、拒答 5/5；引用 115 条 0 偏移/文号错误；解释区禁用措辞 0、判定语气 0；平均 7.2 ms、P95 24.9 ms |
| Provider 自测 | `python -m backend.app.provider --selftest` | PASS 5/5：离线文案合规、拒绝判定资格、拒绝编造文号、在线缺配置回退离线 |
| 日志脱敏自测 | `python -m backend.app.logs --selftest` | PASS 6/6：手机号／身份证／银行卡／邮箱／姓名键／嵌套 |
| 隐私扫描 | `scripts/privacy_check.py` | PASS：audit_log 12 行 + triage_task 11 行，无手机号／身份证／银行卡／邮箱／姓名键明文 |
| 接口冒烟 | `tests/smoke.py` | PASS 63/63，exit 0（新增 7 项：问答模式、引用回点、Provider、域外拒答、健康检查模式、whoami、非法角色 403） |
| 容器 | `docker compose up -d --build`（`fund-agent:0.4`） | PASS：容器 Started；`/api/provider` → `offline`／`qa_v1`；`/api/auth/whoami` → `dev`；`/api/qa` 返回 3 条引用与模式标注 |

### 期间修掉的缺陷
- 冒烟超时：新增访问日志后服务把 stderr 管道写满而阻塞（`tests/smoke.py` 起服务时 `stderr=PIPE` 未消费）。修法：中间件 logger 显式 `setLevel(LOG_LEVEL)`，冒烟启动设 `LOG_LEVEL=WARNING`。
- 评测发现离线文案把条款原文换行带出，禁用词扫描误伤条款内的"符合条件"。修法：摘录前折叠空白，评测把引用原文从解释区剥离后再扫描。
- 评测发现"火星上怎么申请助学金"因关键词"助学金"被命中。修法：加域外提问拦截，拒答原因分 `off_domain`／`no_hit`。

### 未做／未证明
- 在线模型未接真实端点：`FUND_LLM_MODE=online` 的结构校验、重试、回退只做了缺配置回退自测，真实端点时延／一致率／成本 NOT_RUN。
- 事实支持与业务适用的人工核验 NOT_RUN（引用可回点已自动校验，但"条款是否支持该结论"需资助中心逐条看）。
- 加密存储、等保自查、CI 流水线、演示视频仍 NOT_RUN；三页截图仍是 0.3 前版本（本轮只改了问答区模式标注与导出件首部，未重截）。
## v0.5-draft 验收记录（2026-11-07）

本轮把"演示态身份"换成真实账号闭环，并把三条创新点落到代码与页面。

### 已执行并通过

| 检查 | 命令／方式 | 结果 |
|---|---|---|
| 账号闭环 | `python -X utf8 tests/auth_flow.py` | 17 项全过，exit 0：注册 200（pending＋4 步指引）、昵称重复 409、弱密码 422、待审核登录 403、管理员看到申请、驳回缺理由 422、审核通过 200、审核后可登录、`/api/auth/me` 昵称正确、重置密码返回临时密码、旧密码 401、临时密码可登录且 `must_change=true`、本人改密 200、新密码可登录、会话优先于请求头、学生会话访问 `/api/users` 403 |
| 接口冒烟回归 | `python -X utf8 tests/smoke.py` | 63 项全过，exit 0（含问答引用、越界拒答、审计、分档、导出、页面、审计表禁改） |
| 页面联检 | 临时服务 + urllib | `/login.html` 200 且含“注册新账号／注册四步说明”；`/admin.html` 200 含“注册审核／上传并入库”；`/` 200 含“多智能体协同过程”；初判 trace 6/6，cite 核对 5 条引用全部可回点 |
| 上传入库链路 | 临时服务 + urllib | 上传 200（doc 44，pending_verify）→ 重复上传 409 → 未核文切块 400 → 核文 200 → 切块 200（4 条） |
| 版本回滚内容切换 | `python -X utf8 tests/version_flow.py`（8036） | 新版本发布后“量子”命中 3 条且 `version_id` 为新版；回滚旧版后同一问题拒答、`version_id` 为旧版；初判仍取旧版原子快照。10 项全过，exit 0 |
| 问答评测（发布版口径） | `tools/qa_eval.py --report docs/eval/qa_eval_report.md` | 31/31 命中、10/10 边界不判定、9/9 拒答；引用 101 条，偏移／文号不一致 0；禁用措辞 0；PASS |
| 真实库只读核验 | sqlite3 + `/api/qa`（容器 8000） | `policy_snapshot_clause` 93 条、`policy_snapshot_atom` 30 条均属 `pv-20261107T041009`；“师范生”拒答（原文仅在未核文草稿），“国家助学金”命中 3 条且带版本号 |
| 容器 | `docker compose build` / `up -d` | `fund-agent:0.5` 构建成功，`Up (healthy)`；镜像内 `pypdf 5.9.0`；`/login.html` 200、`/api/auth/me` 可查 |
| 截图 | 无头 Edge 1280×760 | `docs/demo/login_page.png`、`admin_registrations.png`、`admin_ingest.png` 已回读核验 |

### 期间修掉的缺陷
- 依赖函数 `request: Request` 未 import，FastAPI 把 `request` 当必填 query，管理接口全 422；补 import 后恢复。
- `/api/auth/login` 用了 `JSONResponse` 但未 import，500 且响应挂住客户端超时；补 `from fastapi.responses import HTMLResponse, JSONResponse, Response`。
- `agents._step` 的标签查表写成了 `dict((k,v) for k,_,v in AGENT_SPEC)`，取到的是描述而非标签；改为显式 `{k: label for k,label,_ in AGENT_SPEC}`。
- cite 代理按条款起止比对偏移，但原子引用存的是全文绝对偏移，导致 5 条全判异常；改为"条款内相对偏移或全文字符切片等于引文"两种都认可，6/6 代理恢复 ok。
- `pc.split_docs()` 自开连接且硬编码 `DB_PATH`，接口传 temp 库时切块写到真实库；给 `connect()`／`split_docs()` 加 `db_path` 参数，接口传 `POLICY_DB`。

### 未做／未证明
- 16 份历史采集草稿已不可被问答或初判引用：检索与原子判等只读“当前发布版本快照”，草稿不进快照；全局 `policy_verified=false` 保持。
- 演示账号无密码，只能请求头演示；手机号无短信验证；`/login.html` 已有“修改密码”表单，但 `must_change` 尚未强制拦截其他页面。
- 在线模型仍 NOT_RUN（用户口径：功能全部完成后再换）；`tools/fact_support.py` 机器核验 26/26 原子通过并导出 `docs/eval/fact_support_review.csv`，但“条款语义是否支持结论”的人工签认列仍空（待资助中心）。
- 三页旧截图为 0.4 前状态，本轮仅新增 login/admin 两页截图；学生端协同 trace 与登录态尚未重截。
## v0.6 部署端口与默认管理员（2026-11-07）

本节记录上线前的端口切分与默认管理员落地，可直接用于部署验收。

| 检查 | 命令／方式 | 结果 |
|---|---|---|
| 四端端口 | `docker compose config` + `docker compose up -d` | 宿主 8000（学生／公共）、8001（教师审核）、8002（管理）、8003（接口）均映射容器 8000；容器 `Up (healthy)` |
| 入口隔离 | 容器四端口 HTTP 实测 | 8000 `/admin.html` 404、8001 `/admin.html` 404、8002 `/review.html` 404、8003 `/login.html` 404；对应端页面 200 |
| 接口隔离 | 容器四端口 HTTP 实测（管理员会话） | 8000 `/api/users`、`/api/policy/docs` 404；8001 `/api/users` 404；8002 `/api/users`、`/api/policy/docs` 200，`/api/triage` 404；8003 `/api/health` 200 |
| 默认管理员 | `POST :8002/api/auth/login`，`nickname=admin`、`password=admin@2026` | 200，返回 `role=admin`、`user=admin`；`/api/auth/me` 为 `source=session`；错误密码 401 |
| 注册页文案 | 容器 8000 `/login.html` 检索 | 不再包含 `138****5678`、`库内只存`；手机号说明改为“选填，仅管理员可见” |
| 账号权限回归 | `tests/smoke.py` | 65 项全过，exit 0；新增“账号列表含默认管理员”“停用普通管理员 200／最后一名管理员不可停用 400／恢复管理员 200” |
| 账号闭环／页面／版本 | `tests/auth_flow.py`、`tests/ui_flow.py`、`tests/version_flow.py` | 17/17、10/10、10/10，exit 0 |

端口规划、反向代理与上线检查见 `docs/部署与端口设计.md`。默认管理员只在账号缺失时创建，不覆盖已修改的密码；首次登录后应改密。

- 三张业务页不再用请求头模拟身份：未登录或角色不匹配时跳转 /login.html；管理端未登录调 /api/users 返回 403。无头 Edge 从 8000 / 打开后落在登录页，截图 docs/demo/login_redirect_check.png。

- 修复四端导航：错误端的页面入口改为 307 跳到对应端（8002 /review.html → 8001、8001 /admin.html → 8002、8000 两个页面分别跳 8001／8002），接口仍 404。实测管理员会话在 8001 /review.html 与 /api/tasks 均 200；8000 /api/users 仍 404。

- 浏览器实测管理员跨端：8002 登录 admin → 点击“任务复核台”→ 自动到 8001 /review.html，页面标题为审核工作台、无“不提供此入口”，截图 docs/demo/admin_review_crossport.png。同时管理端新增“注册审核”直达页签入口。
- 四端 UI 与角色入口审查：学生端移除审核／管理入口；教师端移除学生入口、管理入口仅管理员显示、辅导员隐藏申诉；管理端移除学生入口，审计员改为只读；发布／回滚／停用／注册通过／导出审批／批量复核／申诉裁决增加二次确认。详见 docs/UI审查与角色入口.md；浏览器实测截图 docs/demo/ui_student_nav.png、docs/demo/ui_admin_nav.png。
- 2026-11-07 源码按前后端分层整理：后端 `backend/app`＋`backend/tools`＋`backend/scripts`，前端 `frontend/*.html`＋`frontend/style.css`；启动入口改为 `backend.app.main:app`，Dockerfile 改为 `COPY backend`／`COPY frontend`。整理后 `smoke 65/65`、`auth 17/17`、`ui 10/10`、`version 10/10`，容器 `Up (healthy)`。本文件早于该日期的记录中出现的 `app/`、`tools/`、`scripts/` 为整理前路径，映射见 `docs/代码目录说明.md`。
- 2026-11-07 公网子路径部署完成：`https://zhigang.suit-achieve.cn/ZizhuAgent/`。Caddy 精确 301→`/ZizhuAgent/`，`handle_path` 剥离前缀转发 `127.0.0.1:9100`；容器 `zizhu-agent` healthy；四个页面、`static/style.css`、登录（`Set-Cookie Path=/ZizhuAgent`）、`/api/auth/me`、`/api/health` 均通过；根站标题仍为“产教融合智能教学规划系统”。详见 `docs/公网部署记录.md`。