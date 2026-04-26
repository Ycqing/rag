"""
config.py  —  统一配置
把 API Key 等敏感信息放在 .env 文件中，不要提交到代码仓库
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── 阿里百炼 ──────────────────────────────────────────────
DASHSCOPE_API_KEY   = os.getenv("DASHSCOPE_API_KEY", "sk-xxx")
EMBED_MODEL_NAME    = "text-embedding-v3"   # 百炼 Embedding 模型
LLM_MODEL_NAME      = "qwen-plus"           # 百炼 LLM

# ── Qdrant ────────────────────────────────────────────────
QDRANT_URL          = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY      = os.getenv("QDRANT_API_KEY", "")   # 本地部署留空
COLLECTION_NAME     = "company_kb"          # 统一集合名，所有文档存这里

# ── 角色定义 ──────────────────────────────────────────────
# 角色继承关系：admin > manager > hr / dev > all
ROLE_HIERARCHY = {
    "admin":   {"admin", "manager", "hr", "dev", "all"},
    "manager": {"manager", "hr", "dev", "all"},
    "hr":      {"hr", "all"},
    "dev":     {"dev", "all"},
    "all":     {"all"},
}

def get_accessible_roles(user_role: str) -> list[str]:
    """
    根据用户角色，返回该用户有权限访问的所有角色标签列表。
    例如 admin 可以看到标记为 hr、dev、all 的文档。
    """
    return list(ROLE_HIERARCHY.get(user_role, {"all"}))
