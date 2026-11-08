# 02：本人会话、轮次与上下文

Status: complete（自动验证）
Blocked by: 无
Spec: ../spec.md
Requirements: PC-R02／PC-R04／PC-R09
Acceptance: PC-AC04／05／06／09／10／11／18

## 目标与范围

在 app.db 增加兼容会话／轮次结构，真实 Cookie 身份、owner 校验、request id 幂等、上下文继承／澄清与脱敏持久化。标题用通用主题；旧共享浏览器历史不直接导入。

## 验证要求

同账号恢复与跨账号拒绝、无 Cookie 拒绝、重复请求、缺失与隐私内容留痕检查。删除普通历史与业务审计隔离。现有身份工具可复用，但新入口不得直接依赖演示身份兜底。

## 完成条件

对应 AC 有实际证据，相关旧契约兼容，文档同步，未验证范围明确；实现未开始不得标 done。依赖尚未完成的票不得自行跳过契约直接实施。

## Comments

2026-11-08：由设计迁移生成，未修改业务代码。

## Completion evidence

`tests/test_qa_workbench.py`：归属、幂等、项目继承/切换、隐私最小化、过期执行中断及删除与业务留痕隔离通过。`tests/qa_workbench_flow.py`：无 Cookie 与跨账号访问拒绝通过。
