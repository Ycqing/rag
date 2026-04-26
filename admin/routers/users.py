"""
routers/users.py  —  用户管理 API

GET    /api/users           获取用户列表
POST   /api/users           创建用户
DELETE /api/users/{user_id} 删除用户
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from admin.services import store

router = APIRouter(prefix="/api/users", tags=["users"])

VALID_ROLES = {"all", "hr", "dev", "admin", "manager"}


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str
    name: str


@router.get("")
def list_users():
    return store.list_users()


@router.post("")
def create_user(req: CreateUserRequest):
    if req.role not in VALID_ROLES:
        raise HTTPException(400, f"无效角色: {req.role}，可选: {VALID_ROLES}")
    if store.get_user_by_username(req.username):
        raise HTTPException(409, f"用户名 {req.username} 已存在")
    user_id = store.create_user(req.username, req.password, req.role, req.name)
    return {"user_id": user_id, "message": "创建成功"}


@router.delete("/{user_id}")
def delete_user(user_id: str):
    if not store.delete_user(user_id):
        raise HTTPException(404, "用户不存在")
    return {"message": "删除成功"}
