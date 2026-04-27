"""
admin/services/auth.py  —  admin 端认证工具

提供三个能力：
  1. make_admin_token     登录成功后签发 admin JWT
  2. verify_admin_token   FastAPI Depends，校验 admin JWT，返回 payload
  3. hash_password / verify_password  密码哈希与验证（bcrypt）

admin JWT 使用独立的 ADMIN_JWT_SECRET，与 chat 端用户 JWT 完全隔离。
payload 中携带 is_admin=True 标记，防止用户 token 冒充 admin。
"""

import datetime
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config

# ── Admin JWT ─────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


def make_admin_token(user: dict) -> str:
    """
    登录成功后签发 admin JWT。
    payload 中 is_admin=True，用于区分普通用户 token。
    """
    expire = datetime.datetime.utcnow() + datetime.timedelta(
        hours=config.ADMIN_JWT_EXPIRE_HOURS
    )
    payload = {
        "sub":      user["id"],
        "username": user["username"],
        "role":     user["role"],
        "name":     user["name"],
        "is_admin": True,          # 关键标记，防止用户 token 混用
        "exp":      expire,
    }
    return jwt.encode(payload, config.ADMIN_JWT_SECRET, algorithm=config.ADMIN_JWT_ALGORITHM)


def verify_admin_token(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """
    FastAPI 依赖项，校验 admin JWT 并返回 payload。
    用法：

        @router.get("", dependencies=[Depends(verify_admin_token)])

    或需要获取当前用户信息时：

        @router.get("")
        def list_docs(admin=Depends(verify_admin_token)):
            print(admin["username"])
    """
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录，请先登录管理后台",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(
            cred.credentials,
            config.ADMIN_JWT_SECRET,
            algorithms=[config.ADMIN_JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 无效",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 必须是 admin 角色且带有 is_admin 标记，防止普通用户 token 混用
    if not payload.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足",
        )
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅 admin 角色可访问管理后台",
        )

    return payload


# ── 密码哈希（bcrypt）─────────────────────────────────────

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """返回 bcrypt 哈希字符串，存入数据库"""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """验证明文密码与哈希是否匹配"""
    return _pwd_ctx.verify(plain, hashed)