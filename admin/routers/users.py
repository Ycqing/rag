"""
admin/routers/users.py  —  用户管理 API + admin 登录

POST   /api/users/login     admin 登录，签发 admin JWT（无需认证）
GET    /api/users           获取用户列表（需 admin JWT）
POST   /api/users           创建用户（需 admin JWT）
DELETE /api/users/{user_id} 删除用户（需 admin JWT）

修改说明（相对原版）：
  - 新增 /login 接口，校验密码后签发 admin JWT
  - 所有管理接口加 Depends(verify_admin_token)
  - create_user 调用前先 hash_password
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from admin.services import store
from admin.services.auth import verify_admin_token, make_admin_token, hash_password, verify_password

router = APIRouter(prefix="/api/users", tags=["users"])

VALID_ROLES = {"all", "hr", "dev", "admin", "manager"}


# ── Schema ────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str
    name: str


# ── admin 登录（无需认证）────────────────────────────────

@router.post("/login")
def admin_login(req: LoginRequest):
    """
    admin 端登录接口。
    只有 role='admin' 的用户才能登录管理后台。
    登录成功后返回 admin JWT，后续请求携带在 Authorization: Bearer 头。
    """
    user = store.get_user_by_username(req.username)

    # 用户不存在、密码错误、非 admin 角色，统一返回同一错误信息，防止枚举
    if (
        not user
        or not verify_password(req.password, user["password"])
        or user["role"] != "admin"
    ):
        raise HTTPException(401, "用户名或密码错误")

    token = make_admin_token(user)
    return {
        "token":    token,
        "username": user["username"],
        "name":     user["name"],
        "role":     user["role"],
    }


# ── 用户管理（均需 admin JWT）────────────────────────────

@router.get("", dependencies=[Depends(verify_admin_token)])
def list_users():
    return store.list_users()


@router.post("", dependencies=[Depends(verify_admin_token)])
def create_user(req: CreateUserRequest):
    if req.role not in VALID_ROLES:
        raise HTTPException(400, f"无效角色: {req.role}，可选: {VALID_ROLES}")
    if store.get_user_by_username(req.username):
        raise HTTPException(409, f"用户名 {req.username} 已存在")

    hashed = hash_password(req.password)
    user_id = store.create_user(req.username, hashed, req.role, req.name)
    return {"user_id": user_id, "message": "创建成功"}


@router.delete("/{user_id}", dependencies=[Depends(verify_admin_token)])
def delete_user(user_id: str):
    if not store.delete_user(user_id):
        raise HTTPException(404, "用户不存在")
    return {"message": "删除成功"}