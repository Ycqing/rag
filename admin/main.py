"""
admin/main.py  —  管理后台 FastAPI 主入口

启动方式：
    cd rag_rbac
    uvicorn admin.main:app --reload --port 8001

访问：http://localhost:8001

修改说明（相对原版）：
  - 新增 GET /api/admin/me，前端登录后用于获取当前 admin 信息
  - 路由认证通过各 router 内的 Depends(verify_admin_token) 声明
  - 静态文件、SPA 路由、启动事件完全不变
"""

from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from admin.services.store import init_db
from admin.services.auth import verify_admin_token
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

# 当前登录的 admin 信息（供前端顶栏展示用）
@app.get("/api/admin/me")
def admin_me(admin=Depends(verify_admin_token)):
    return {
        "username": admin["username"],
        "name":     admin["name"],
        "role":     admin["role"],
    }

# 挂载静态文件目录
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 所有未匹配路由都返回前端页面（SPA 路由支持）
@app.get("/")
@app.get("/{full_path:path}")
def serve_frontend(full_path: str = ""):
    index = STATIC_DIR / "index.html"
    return FileResponse(index)