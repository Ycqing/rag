"""
services/store.py  —  文档元数据持久化（SQLite）

存储每个文档的：文件名、角色权限、节点数、索引状态、上传时间
向量数据在 Qdrant 里，这里只存管理信息
"""

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "db" / "admin.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 让结果可以用列名访问
    return conn


def init_db():
    """初始化数据库表结构，应用启动时调用一次"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id          TEXT PRIMARY KEY,
            filename    TEXT NOT NULL,
            doc_type    TEXT NOT NULL,
            roles       TEXT NOT NULL,      -- JSON 数组字符串，如 '["hr","admin"]'
            node_count  INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'pending',  -- pending / indexing / indexed / error
            error_msg   TEXT,
            file_size   INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            username    TEXT UNIQUE NOT NULL,
            password    TEXT NOT NULL,      -- 实际项目用 bcrypt，这里明文演示
            role        TEXT NOT NULL,
            name        TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    # 预置测试用户
    for u in [
        ("alice",   "test123", "hr",      "Alice（HR）"),
        ("bob",     "test123", "dev",     "Bob（研发）"),
        ("charlie", "test123", "admin",   "Charlie（管理员）"),
        ("david",   "test123", "all",     "David（普通员工）"),
    ]:
        conn.execute("""
            INSERT OR IGNORE INTO users(id,username,password,role,name,created_at)
            VALUES(?,?,?,?,?,?)
        """, (str(uuid.uuid4()), u[0], u[1], u[2], u[3], _now()))
    conn.commit()
    conn.close()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 文档 CRUD ─────────────────────────────────────────────────────────────────

def create_document(filename: str, doc_type: str, roles: list[str], file_size: int) -> str:
    doc_id = str(uuid.uuid4())
    import json
    conn = get_conn()
    conn.execute("""
        INSERT INTO documents(id,filename,doc_type,roles,file_size,status,created_at,updated_at)
        VALUES(?,?,?,?,?,'pending',?,?)
    """, (doc_id, filename, doc_type, json.dumps(roles, ensure_ascii=False), file_size, _now(), _now()))
    conn.commit()
    conn.close()
    return doc_id


def update_document_status(doc_id: str, status: str, node_count: int = 0, error_msg: str = None):
    conn = get_conn()
    conn.execute("""
        UPDATE documents SET status=?, node_count=?, error_msg=?, updated_at=?
        WHERE id=?
    """, (status, node_count, error_msg, _now(), doc_id))
    conn.commit()
    conn.close()


def delete_document(doc_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def list_documents() -> list[dict]:
    import json
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM documents ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["roles"] = json.loads(d["roles"])
        result.append(d)
    return result


def get_document(doc_id: str) -> dict | None:
    import json
    conn = get_conn()
    row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["roles"] = json.loads(d["roles"])
    return d


# ── 用户 CRUD ─────────────────────────────────────────────────────────────────

def list_users() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT id,username,role,name,created_at FROM users ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_user(username: str, password: str, role: str, name: str) -> str:
    user_id = str(uuid.uuid4())
    conn = get_conn()
    conn.execute("""
        INSERT INTO users(id,username,password,role,name,created_at)
        VALUES(?,?,?,?,?,?)
    """, (user_id, username, password, role, name, _now()))
    conn.commit()
    conn.close()
    return user_id


def delete_user(user_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_user_by_username(username: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── 统计 ──────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    conn = get_conn()
    total_docs  = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    total_nodes = conn.execute("SELECT SUM(node_count) FROM documents").fetchone()[0] or 0
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    indexed     = conn.execute("SELECT COUNT(*) FROM documents WHERE status='indexed'").fetchone()[0]
    pending     = conn.execute("SELECT COUNT(*) FROM documents WHERE status IN ('pending','indexing')").fetchone()[0]
    errors      = conn.execute("SELECT COUNT(*) FROM documents WHERE status='error'").fetchone()[0]
    conn.close()
    return {
        "total_docs":  total_docs,
        "total_nodes": total_nodes,
        "total_users": total_users,
        "indexed":     indexed,
        "pending":     pending,
        "errors":      errors,
    }
