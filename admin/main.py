"""
admin/main.py  —  管理后台 FastAPI 主入口

启动方式：
    cd rag_rbac
    uvicorn admin.main:app --reload --port 8001

访问：http://localhost:8001
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from admin.services.store import init_db
from admin.routers import docs, users

app = FastAPI(title="星辰科技知识库管理后台", version="1.0.0")

# 启动时初始化数据库
@app.on_event("startup")
def on_startup():
    init_db()
    print("数据库初始化完成")

# 注册路由
app.include_router(docs.router)
app.include_router(users.router)

# 挂载静态文件目录
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 所有未匹配路由都返回前端页面（SPA 路由支持）
@app.get("/")
@app.get("/{full_path:path}")
def serve_frontend(full_path: str = ""):
    index = STATIC_DIR / "index.html"
    return FileResponse(index)
