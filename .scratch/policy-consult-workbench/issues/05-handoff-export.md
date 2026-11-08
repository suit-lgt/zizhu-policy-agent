# 05：本人初判联动与咨询导出

Status: complete（自动验证）
Blocked by: 无
Spec: ../spec.md
Requirements: PC-R07／PC-R08／PC-R09
Acceptance: PC-AC09／15／16／17／18

## 目标与范围

提供 owner 约束的最小 handoff，只带确认过的项目／字段线索；triage.html 继续核对与 consent；progress.html 仍按原流程操作。单轮／全会话个人导出明确范围、引用、版本和真实模式。

## 验证要求

URL 和本地存储不含家庭明细；非法 task／turn 拒绝；业务审计不随聊天删除；导出不替代对外报送审批。优先沿用项目现有文件格式，不为迁移强制引入 DOCX。

## 完成条件

对应 AC 有实际证据，相关旧契约兼容，文档同步，未验证范围明确；实现未开始不得标 done。依赖尚未完成的票不得自行跳过契约直接实施。

## Comments

2026-11-08：由设计迁移生成，未修改业务代码。

## Completion evidence

隔离接口测试确认 handoff 只带项目线索/版本、不创建任务；导出限会话归属且带版本、模式、引用和面板快照；跨账号读取拒绝。初判页面仅提示并要求重新核对与 consent。
