# 版本说明 v0.8.0

日期：2026-11-19
基线：v0.7.0（2026-11-19）之后的增量版本；技术栈仍为 FastAPI＋SQLite＋多页 HTML，未更换。
项目：资助政策智能体（面向学生、家庭与高校老师的资助政策资格初判与问答系统）
Git 标签：`v0.8.0`；`v0.7.0` 作为前一版本标签保留，两个标签说明各自独立。

## 本次版本包含

1. **政策闯关小游戏**（`frontend/game.html`／`game.css`／`game.js`）：6 关情景判断，玩家判断某个学生情形属于「可申请／需核实／暂不适用」哪一档，作答后展示对应条文与原文回点。手机竖屏优先，单局 3–5 分钟，免登录。
2. **题库不存答案**（`data/game/levels.json`）：题库只存情景、学生画像、线索与解析，**不含正确答案**；结论由服务端 `triage_one()` 现算，规则变更后游戏自动同步，也守住「三档结论只能由程序给出」。
3. **接口**（`backend/app/game_routes.py`）：`GET /api/game/levels` 脱敏下发（不含画像、解析、结论）；`POST /api/game/answer` 返回正误、实际三档、`reasons`（含 `clause_id` 与原文偏移）。内部直调判等函数，不走 HTTP，不需要登录。
4. **游戏规则**：信任值 3 点，答错一关熄灭一颗，归零即结束；答对 +150／关，全部答对额外 +100，满分 1000；线索每关 1 次、不扣分。最高分只存浏览器 `localStorage`，不落库、不采集学生信息。
5. **入口**：四个学生页导航与首页快捷入口新增「政策闯关」（首页快捷入口调整为 2×2 布局）。
6. **测试**：新增 `tests/game_flow.py`（25 项）；`tests/smoke.py` 页面断言新增 `/game.html`。
7. **文档**：本文件改写为 v0.8.0；`README.md`、`frontend/README.md`、`docs/开发文档.md`、`docs/代码目录说明.md`、`docs/verification.md` 同步新增页面、接口与验证记录；策划规格见 `.scratch/policy-game/spec.md`。

## 验证状态

2026-11-19 在项目环境 `zizhuagent/python.exe`（Python 3.11.18）重跑；下表命令中的 `python` 均指该解释器。

| 检查 | 命令 | 结果 |
|---|---|---|
| 闯关联检 | `python -X utf8 tests/game_flow.py` | PASS 25/25 |
| 接口冒烟回归 | `python -X utf8 tests/smoke.py` | PASS 66/66 |
| Provider 自测 | `python -X utf8 -m backend.app.provider --selftest` | PASS 12/12 |
| 工作台单元与契约 | `python -X utf8 -m unittest tests.test_qa_workbench` | PASS 19/19 |
| 前端脚本语法 | `node --check frontend/game.js` | PASS |
| 渲染与横向溢出 | Chromium headless，390／430 两档宽度，五态截图并量 `body.scrollWidth` | PASS：等于视口宽，无超宽元素 |

六关结论均由服务端 `triage_one()` 实际输出，非人工推断：

| 关 | 情景 | 项目 | 结论 |
|---|---|---|---|
| L1 | 一年级专业第一申国奖 | GJ | 暂不适用 |
| L2 | 认定结论未出申励志奖学金 | LZ | 需核实 |
| L3 | 退役士兵读大一、未做困难认定 | GZ | 可申请 |
| L4 | 外省生源申广东新生资助 | XS | 暂不适用 |
| L5 | 孤儿且特别困难申校内减免 | XN | 可申请 |
| L6 | 已获省其他专项申新生资助 | XS | 需核实 |

## 部署说明

本版未改动 `Dockerfile`／`docker-compose.yml`，沿用 v0.6.0 的部署结构。**本轮的镜像构建与公网发布无记录（NOT_RUN）；不能据此认为公网已运行 v0.8.0，上线前须另行构建、部署并核对。**

历史部署（v0.6.0，详见 `docs/公网部署记录.md` 与 `docs/部署与端口设计.md`）：

- 镜像 `fund-agent:0.6`；容器 `zizhu-agent`，仅绑定 `127.0.0.1:9100`
- 数据卷：服务器 `./data:/app/data`
- 反向代理：Caddy `/ZizhuAgent/*` 剥离前缀转发，`X-Forwarded-Prefix` 为 `/ZizhuAgent`；公网子路径 `https://zhigang.suit-achieve.cn/ZizhuAgent/`，根站不受影响
- 本地四端端口 8000 学生／8001 教师／8002 管理／8003 接口
- 默认管理员 `admin`；生产环境请立即修改默认密码

## 已知边界

- 六关题面表述是否会引起误解，**未经资助中心人员签认**；真机竖屏（iOS／Android 各一台）、≥5 名真人试玩、键盘全流程无障碍、摆摊实际场景均 NOT_RUN；仅验证 Chromium 内核，Firefox／Safari 未测。
- 游戏分数与真实资助资格**无任何联动**；最高分只存本地，多设备不同步，未做账号绑定（有意为之，避免采集学生信息）。
- 真实端点 50 题回归、越界措辞抽样、时延／成本记录、资助中心人工语义签认、学生实操仍 NOT_RUN；单链路实测不等于在线达标。
- 事实守恒校验对「不含数字／文号等标记词的新事实陈述」存在漏检盲区，以实体白名单＋人工语义签认兜底。
- 19 份政策中 3 份核心已核文并发布，其余 16 份仍 `pending_verify`，全局 `policy_verified=false`；未核文内容不作为权威结论。
- 演示视频、浏览器截图复核未做（按 `docs/verification.md` 口径）。
- `docs/ai-contract.md` 末句「真实在线端点评测仍为 NOT_RUN」指 50 题回归，与单链路实测不矛盾。

---

## v0.7.0 版本内容（历史）

日期：2026-11-19
基线：v0.6.0（2026-11-07）之后的增量版本。
Git 标签：`v0.7.0`。

### 版本包含

1. 事实守恒的语言组织（ADR-0002）：程序确定性生成 FactPack（事实包），模型只把给定 `facts` 组织成自然语言，无权自行检索、挑选或增删事实；输出 `{answer, claims}`，逐句以 `[fact_id]` 锚定。
2. 五层机器校验：结构／锚点／事实原子（数字·金额·日期·文号·条款号 ⊆ FactPack）／结论三档守恒／措辞与敏感信息。任一失败不展示，重试后回退离线模板并记 `fallback_reason_code`；引用仍由系统拼装、可回点原文。
3. 在线模式：端点沿用 OpenAI 兼容 `chat/completions` 与 `FUND_LLM_*` 环境变量，不新增第三方依赖；`offline` 仍为默认。DeepSeek `deepseek-chat` 单条链路实测 `state=completed`、`mode=online`、`fallback=None`。
4. 判等边界不变：三档结论由程序给出，模型不新增或改写个人结论；页面恒显「AI 初判仅供参考，最终以资助部门认定为准」。
5. 系统架构图重制：`docs/资助政策智能体系统架构图.html`（单文件、内嵌可访问 SVG）与同名 `.svg`，1920×1160，可离线打开；未把未部署组件画成已部署能力。
6. 文档与规格：新增 `docs/adr/0002-fact-grounded-language.md`；补 `docs/verification.md` 实测记录；校正 spec／ADR 的实施状态与 NOT_RUN 口径；`README.md`、`docs/ai-contract.md` 同步在线模式描述。
7. 工程：`.gitignore` 忽略本地运行环境 `zizhuagent/`；数据库、上传附件、备份、日志、`.env` 均不入库。

### 当时的验证状态

2026-11-19 上传前在项目环境 `zizhuagent/python.exe`（Python 3.11.18）重跑。

| 检查 | 命令 | 结果 |
|---|---|---|
| Provider 自测（含编造注入负样本） | `python -X utf8 -m backend.app.provider --selftest` | PASS 12/12 |
| 工作台单元与契约 | `python -X utf8 -m unittest tests.test_qa_workbench` | PASS 19/19 |
| 接口冒烟回归 | `python -X utf8 tests/smoke.py` | PASS 65/65 |
| 工作台端到端 | `python -X utf8 tests/qa_workbench_flow.py` | PASS 11/11 |

另据 `docs/verification.md`：真实在线端点单条链路实测通过（DeepSeek `deepseek-chat`）。v0.6.0 时期的 `tests/auth_flow.py` 17/17、`tests/ui_flow.py` 10/10、`tests/version_flow.py` 10/10、问答评测 50 题与事实支持机器核验 26/26 属此前已验证项，当时未重跑。

### 当时的已知边界

- 真实端点 50 题回归、越界措辞抽样、时延／成本记录、资助中心人工语义签认、学生实操均 NOT_RUN。
- 19 份政策中 3 份核心已核文并发布，其余 16 份仍 `pending_verify`。
- 演示视频、浏览器截图复核未做。
- 公网当前为单子路径入口；本地四端端口仍保留。
