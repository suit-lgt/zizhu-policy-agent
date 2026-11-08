# 后端（backend）

本目录只放服务端代码，前端页面在项目根 `frontend/`。

| 目录 | 内容 |
|---|---|
| `app/` | FastAPI 运行包：`main.py` 路由与入口守卫，`db.py` 业务库，`accounts.py` 注册／登录／角色，`qa.py` 快照检索与问答，`agents.py` 多智能体协同，`ingest.py` 本地上传入库，`triage` 相关工具在 `tools/` |
| `tools/` | 政策采集、条款切块、原子化、判等、问答评测、事实支持核查 |
| `scripts/` | 初始化、备份、恢复、隐私扫描 |

启动（项目根执行）：

```powershell
python -X utf8 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

统一用项目根作为工作目录，`data/` 与 `frontend/` 通过 `main.py` 里的项目根定位，不依赖当前目录。
