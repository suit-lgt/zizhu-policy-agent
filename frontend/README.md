# 前端（frontend）

本目录只放浏览器端静态页面与样式，由后端 `backend/app/main.py` 挂载到 `/static` 并提供页面路由。

| 文件 | 用途 |
|---|---|
| `login.html` | 登录／注册；登录后按角色跳转 8000／8001／8002 |
| `index.html` | 学生端工作台：最近办理、任务统计、三个功能入口 |
| `triage.html` | AI 初判独立页：情况填写、结论、依据条、材料清单 |
| `qa.html` + `qa-workbench.js` + `qa-workbench.css` | 政策咨询工作台：本人会话、连续追问、同版原文、办理依据、导出与初判引导 |
| `progress.html` | 办理进度独立页：状态、复核／申诉、材料包与对外报送 |
| `review.html` | 教师端：待复核任务、批量初筛、两级看板、预警；管理员可见申诉裁决 |
| `admin.html` | 管理端：政策核文、文档入库、注册审核、版本回滚、账号权限、导出审批、报表 |
| `style.css` | 四端共用样式 |

页面不保存角色身份，只依赖后端 `fund_session` HttpOnly Cookie；角色可见性由页面脚本与后端权限双重约束。
