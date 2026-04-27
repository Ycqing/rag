"""
config.py  —  统一配置
敏感信息全部从 .env 读取，代码库中不存任何密钥
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── 阿里百炼 ──────────────────────────────────────────────
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-xxx")
EMBED_MODEL_NAME  = "text-embedding-v3"
LLM_MODEL_NAME    = "qwen-plus"

# ── Qdrant ────────────────────────────────────────────────
QDRANT_URL        = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY", "")
COLLECTION_NAME   = "company_kb"

# ── MySQL ──────────────────────────────────────────────────
MYSQL_HOST        = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT        = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER        = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD    = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE    = os.getenv("MYSQL_DATABASE", "startech_kb")

# ── JWT（chat 端用户登录）─────────────────────────────────
_jwt_secret = os.getenv("JWT_SECRET", "")
if not _jwt_secret:
    raise RuntimeError(
        "[config] JWT_SECRET 未配置，拒绝启动。\n"
        "生成命令：python -c \"import secrets; print(secrets.token_hex(32))\""
    )
JWT_SECRET       = _jwt_secret
JWT_ALGORITHM    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

# ── Admin JWT（admin 端登录，与用户 JWT 完全隔离）──────────
# 使用独立密钥，即使用户 JWT 密钥泄露也不影响 admin 端
_admin_jwt_secret = os.getenv("ADMIN_JWT_SECRET", "")
if not _admin_jwt_secret:
    raise RuntimeError(
        "[config] ADMIN_JWT_SECRET 未配置，拒绝启动。\n"
        "生成命令：python -c \"import secrets; print(secrets.token_hex(32))\""
    )
ADMIN_JWT_SECRET       = _admin_jwt_secret
ADMIN_JWT_ALGORITHM    = "HS256"
ADMIN_JWT_EXPIRE_HOURS = int(os.getenv("ADMIN_JWT_EXPIRE_HOURS", "8"))

# ── 角色定义 ──────────────────────────────────────────────
ROLE_HIERARCHY = {
    "admin":   {"admin", "manager", "hr", "dev", "all"},
    "manager": {"manager", "hr", "dev", "all"},
    "hr":      {"hr", "all"},
    "dev":     {"dev", "all"},
    "all":     {"all"},
}

def get_accessible_roles(user_role: str) -> list[str]:
    return list(ROLE_HIERARCHY.get(user_role, {"all"}))