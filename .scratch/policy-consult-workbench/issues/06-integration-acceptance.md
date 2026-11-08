# 06：整链回归与交付证据

Status: complete（自动化范围）
Blocked by: 无
Spec: ../spec.md
Requirements: PC-R01—PC-R10
Acceptance: PC-AC01—PC-AC22

## 目标与范围

在临时库副本上贯通会话→证据→回答→原文→初判／进度→导出；执行既有冒烟、账号、页面、版本与问答评测，新增语义支持度检查和操作验收。

## 验证要求

分开记录自动 PASS、真实在线 NOT_RUN、人工语义签认和学生实操；核对旧数据迁移与部署子路径。没有发布授权不部署，不能把既有通过数复用为本次结果。

## 完成条件

对应 AC 有实际证据，相关旧契约兼容，文档同步，未验证范围明确；实现未开始不得标 done。依赖尚未完成的票不得自行跳过契约直接实施。

## Comments

2026-11-08：由设计迁移生成，未修改业务代码。

## Completion evidence

2026-11-08：`test_qa_workbench.py` 18/18、`qa_workbench_flow.py` 11/11、`smoke.py` 65/65、`version_flow.py`、`auth_flow.py` 17/17、`ui_flow.py`、`qa_eval.py` 50/50 与 `docker compose config` 通过。真实在线端点、人工语义签认、学生实操和真实窄屏浏览器操作为 NOT_RUN；未部署。
