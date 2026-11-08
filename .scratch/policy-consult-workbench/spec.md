# 功能规格：政策咨询与办理工作台

Status: implemented（自动验证完成；真实在线模型、人工语义核验与学生实操仍未执行）
日期：2026-11-08。范围：扩展原 qualification-triage/spec.md 的 R-005；复用 R-003／004／007／010。
主设计：[政策咨询工作台迁移设计](../../docs/政策咨询工作台迁移设计.md)。AI／数据增量约束以 docs/ai-contract.md、docs/data-contract.md 的本轮增补为准。

## 目标与非目标

学生通过连续问答理解政策，查看同版证据、待补条件、材料与办理步骤，再进入本人初判或进度流程。
不在自由问答中产生新的个人资格结论；不替代复核、认定或发放；不改现有技术栈；不默认扩展政策范围。

## 需求

| ID | 行为 | 优先级 |
|---|---|---|
| PC-R01 | 使用同一发布版本、适用范围和原文偏移形成证据包，拒绝低相关／无依据结论 | P0 |
| PC-R02 | 五意图路由与有界多轮上下文；个人事实须确认，不从聊天自动判等 | P0 |
| PC-R03 | 回答、办理面板、引用、历史和导出共用当轮快照 | P0 |
| PC-R04 | 本人会话和轮次持久化、幂等、终态与恢复；无共享匿名历史 | P0 |
| PC-R05 | 先展示确定性证据，完整校验后分块发送解释 | P0 |
| PC-R06 | 版本限定原文回看及明确的旧版／新版切换 | P0 |
| PC-R07 | 本人咨询进入初判／进度，确认与权限仍执行 | P1 |
| PC-R08 | 单轮／全会话个人导出，标明模式、版本与限制，不绕过报送审批 | P1 |
| PC-R09 | 最小持久化、脱敏、无敏感日志，删除不影响业务审计 | P0 |
| PC-R10 | 旧 /api/qa 与既有评测兼容，真实模型结果分开验证 | P0 |

## 关键契约

QueryContext：intent ∈ policy_scope／benefit_standard／materials／process／review_progress；控制分支 clarify／refused。项目代码和字段必须在既有字典中；保留字段来源与确认状态。task_id 必须本人有权访问。

版本：一般咨询第一业务轮固定当前发布版；以后继承，显式切新版才改变。本人的历史任务解释使用任务版本＋结果快照；现在重新初判走当前版原流程。引用复合标识至少包含 version_id 与 clause_id，历史源不可查当前表替代。

EvidenceBundle：context、policy_items、可选 rule_result、missing_fields、materials／procedure／benefit、scope_status、warnings、actions；材料与校级配置须注明来源、确认状态、当轮快照。当前版但有效期／范围元数据缺失时不得装作已核实适用。

正文：所有资格结果来自 rule_result；没关联结果时只能解释条件与引导。程序构造 citations；模型不得生成文号、判等、材料要求或办理期限。原文可包含政策用语，禁止措辞检查针对模型解释区，不篡改原文引文。

## 实现实体

- qa_session：id、owner_user_id、topic、created_at、updated_at；标题由通用项目主题生成。
- qa_turn：id、session_id、client_request_id、parent_turn_id、sanitized_question、context_json、evidence_json、panel_json、validated_answer、citations_json、task_json、state、mode、fallback_reason_code、prompt_version、created_at、completed_at。
- qa_handoff：id、owner_user_id、source_turn_id、context_json、expires_at、consumed_task_id；目前只携带项目线索和版本，不创建任务。

约束：session_id＋client_request_id 唯一；parent_turn 必须同会话同属主；task_snapshot 必须访问授权；执行前固定版本，后续不重新读 current 决定本轮口径。普通会话删除不级联业务审计，业务任务保留必要最小来源。

## 状态与终态

accepted → preparing → validating → completed。
明确分支 clarified／refused／failed／cancelled／interrupted。每个业务终态落库，completed 先落库后发送 done。

重复同 client_request_id 返回已有 turn／状态，不重复创建。真正重试另起 attempt 并关联旧轮；客户端刷新只查状态不重复提问。服务重启将悬挂轮标 interrupted，不自动报告完成。

流：meta → chunk* → citations → done(status,turn_id)。meta 只含已核验结构化数据；正文完整 validate 后才切片。无 done 的断流不视为成功，查询已落库状态恢复。不得在数据库写锁内等待模型。

## 前端与路径

qa.html 实现本人记录／问答／办理依据区域，复用 style.css；移动端折叠。全部新 API 沿用相对路径和 Cookie；测试根路径与现有反代子路径。旧共享 HISTORY_KEY 清理，不自动归属登录账号。

右侧动作带 owner 验证的 turn_id／task_id。去初判只预选项目和确认线索，保持表单核对与 consent；去进度复用现有流程。单轮导出必须明确 turn_id，全会话必须 scope=all。

## 验收

| ID | 需求 | Given／When | Then |
|---|---|---|---|
| PC-AC01 | R01/R06 | 已发布 V1 与未核文草稿都含同词；咨询 | 只用允许的 V1，草稿无机会进入答案／面板 |
| PC-AC02 | R01/R06 | V1 回答后发布 V2 修改同条款；点击旧引用／导出 | 展示 V1 原文与偏移，提示更新但不替换 |
| PC-AC03 | R01 | 无当前版、无命中、仅低相关 2 字碰巧命中 | 分别明确无证据／范围不明，不因命中多就高可信 |
| PC-AC04 | R02 | 问项目 A 条件，追问“要什么材料”，再改问项目 B | 继承 A 后显式切 B；不串项目或混合政策版 |
| PC-AC05 | R02/R09 | 聊天写家庭收入／身份、问“我能申请吗” | 不自动写资料或判等；去初判确认；日志／审计无原文 |
| PC-AC06 | R01/R02 | 需要评审字段缺失，校级档位无已确认细则 | 标为待认定／待核实；模型不补事实和映射 |
| PC-AC07 | R03 | 政策材料、校级配置、演示项同时出现 | 各自标明来源，演示项不成为必需材料结论 |
| PC-AC08 | R03/R06 | 新会话首次问答后配置更新，回看旧轮 | 旧清单与步骤使用保存快照，当前配置不污染 |
| PC-AC09 | R04/R09 | A 请求 B 的会话／轮／证据／导出／handoff | 全部拒绝；教师／管理员身份不默认获取私人聊天 |
| PC-AC10 | R04/R09 | 无 Cookie，仅提供演示身份头访问新会话 | 拒绝；正式咨询不借演示身份绕过 |
| PC-AC11 | R04 | 同 request id 重试／刷新／切会话 | 只产生一轮，不串流，刷新恢复已存状态 |
| PC-AC12 | R05 | 模型输出假金额／越界引用／改写个人结论 | 校验失败，未向学生泄露原始非法片段，有限重试后明确降级 |
| PC-AC13 | R04/R05 | 首包前故障、验证失败、网络断流、进程重启 | 明确失败／中断状态，不把半截内容报告完成 |
| PC-AC14 | R05/R10 | offline／mock／online缺配置，政策数据可用 | 证据链仍可用，显示真实模式与回退原因；不声称真实在线已测 |
| PC-AC15 | R07 | 从指定轮去初判 | owner、版本、项目验证；预填需确认；未确认不创建初判／复核 |
| PC-AC16 | R07 | 解释本人旧任务，再要求按现在重判 | 旧任务不变；经用户确认进入当前版初判 |
| PC-AC17 | R08 | 导出第 2 轮与全会话 | 范围精确；版号、模式、引用与面板一致，无家庭明文 |
| PC-AC18 | R09 | 删除会话，存在相关复核／申诉 | 私人历史按规则删除，业务留痕与必要来源不被删除 |
| PC-AC19 | R10 | 旧 /api/qa 调用及既有测试集 | 保留兼容结构；不得改断言掩盖退化；有意改变附迁移依据 |
| PC-AC20 | R03/R10 | 窄屏／桌面、根路径／反代子路径、过期登录 | 关键任务可操作；路径正确；认证失败明确跳登录 |
| PC-AC21 | R01/R06 | 有效期／适用范围缺失或冲突；问当前适用 | 说明待核实，不用“已发布”代替有效性判断 |
| PC-AC22 | R01/R03 | 正文文号都真实但结论与引用不相干 | 支持度检查发现；引用存在性不能单独作为 PASS |

## 指标与样例

开发用合成学生，不新增真实家庭数据。真实政策原文复用项目已核验版本，不编金额、日期和学校规定。样例项目以 A／B 命名，用明确测试夹具，不冒充真实政策。

验收分：结构／版本／权限自动验证，答案语义人工抽检，真实模型端点评测，学生实操；后三者没有执行就记 NOT_RUN。命中条数不代表置信概率，办理结果不得以问卷比例代替系统成效。

## 未决与非阻塞处理

会话保留期限尚无用户指定：首版不自动清除普通历史，提供本人删除，正式上线前明确期限；审计另按既有不可删约束。
学校时限／渠道／材料细则未齐全：显示待学校确认，不阻止基础政策解释；不得编造。
真实在线模型按现有计划后接入，本轮设计不要求购买或启用新服务。

## 2026-11-08 实现与自动验收

实现位于 `backend/app/qa_context.py`、`qa_evidence.py`、`qa_sessions.py`、`qa_panel.py`、`qa_routes.py`，页面位于 `frontend/qa.html`、`qa-workbench.js`、`qa-workbench.css`。旧 `POST /api/qa` 保持兼容；新入口只接受真实 `fund_session` Cookie。

`tests/test_qa_workbench.py` 覆盖快照版本隔离、弱匹配拒答、材料来源降级、多轮上下文、幂等、最小化持久化、重启中断、面板快照与模型选择校验，共 18 项通过。`tests/qa_workbench_flow.py` 在复制库和两名合成账号上贯通 Cookie、会话、轮次、事件流、跨账号拒绝、版本证据、导出和初判引导，共 11 项通过。

PC-AC20 的真实窄屏浏览器操作、在线端点评测、人工语义支持度与学生实操保持 NOT_RUN；自动测试不能替代这些验收。
