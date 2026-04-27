"""
admin/services/store.py  —  文档元数据 + 用户持久化（MySQL）

密码字段统一使用 bcrypt 哈希，明文不落库。
哈希/验证逻辑集中在 admin/services/auth.py，store 只负责读写。
"""

import json
import uuid
import threading
from datetime import datetime

import pymysql
import pymysql.cursors

import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
import config

# ── 线程级连接复用 ────────────────────────────────────────
_local = threading.local()


def get_conn() -> pymysql.connections.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None or not conn.open:
        conn = _new_conn()
        _local.conn = conn
    else:
        try:
            conn.ping(reconnect=True)
        except Exception:
            conn = _new_conn()
            _local.conn = conn
    return conn


def _new_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


# ── 初始化表结构 ───────────────────────────────────────────

def init_db():
    """
    初始化数据库表结构，应用启动时调用一次。
    预置用户密码使用 bcrypt 哈希，明文不入库。
    """
    # 延迟导入，避免循环依赖
    from admin.services.auth import hash_password

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id          VARCHAR(36)  PRIMARY KEY,
                filename    VARCHAR(512) NOT NULL,
                doc_type    VARCHAR(20)  NOT NULL,
                roles       TEXT         NOT NULL,
                node_count  INT          DEFAULT 0,
                status      VARCHAR(20)  DEFAULT 'pending',
                error_msg   TEXT,
                file_size   INT          DEFAULT 0,
                created_at  DATETIME     NOT NULL,
                updated_at  DATETIME     NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          VARCHAR(36)  PRIMARY KEY,
                username    VARCHAR(128) UNIQUE NOT NULL,
                password    VARCHAR(256) NOT NULL COMMENT 'bcrypt 哈希',
                role        VARCHAR(32)  NOT NULL,
                name        VARCHAR(128) NOT NULL,
                created_at  DATETIME     NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)

        # 预置测试用户，密码使用 bcrypt 哈希
        preset_users = [
            ("alice",   "test123", "hr",    "Alice（HR）"),
            ("bob",     "test123", "dev",   "Bob（研发）"),
            ("charlie", "test123", "admin", "Charlie（管理员）"),
            ("david",   "test123", "all",   "David（普通员工）"),
        ]
        for username, plain_pwd, role, name in preset_users:
            cur.execute("""
                INSERT IGNORE INTO users (id, username, password, role, name, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), username, hash_password(plain_pwd), role, name, _now()))

    conn.commit()


# ── 工具函数 ──────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _row_to_dict(row: dict) -> dict:
    """将 MySQL Row 中的 datetime 统一转为字符串"""
    d = dict(row)
    for k in ("created_at", "updated_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].strftime("%Y-%m-%d %H:%M:%S")
    return d


# ══════════════════════════════════════════════════════════
# 文档 CRUD
# ══════════════════════════════════════════════════════════

def create_document(filename: str, doc_type: str, roles: list[str], file_size: int) -> str:
    doc_id = str(uuid.uuid4())
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO documents
                (id, filename, doc_type, roles, file_size, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)
        """, (doc_id, filename, doc_type,
              json.dumps(roles, ensure_ascii=False),
              file_size, _now(), _now()))
    conn.commit()
    return doc_id


def update_document_status(doc_id: str, status: str, node_count: int = 0, error_msg: str = None):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE documents SET status=%s, node_count=%s, error_msg=%s, updated_at=%s
            WHERE id=%s
        """, (status, node_count, error_msg, _now(), doc_id))
    conn.commit()


def delete_document(doc_id: str) -> bool:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
        affected = cur.rowcount
    conn.commit()
    return affected > 0


def list_documents() -> list[dict]:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM documents ORDER BY created_at DESC")
        rows = cur.fetchall()
    result = []
    for row in rows:
        d = _row_to_dict(row)
        if isinstance(d["roles"], str):
            d["roles"] = json.loads(d["roles"])
        result.append(d)
    return result


def get_document(doc_id: str) -> dict | None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM documents WHERE id=%s", (doc_id,))
        row = cur.fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    if isinstance(d["roles"], str):
        d["roles"] = json.loads(d["roles"])
    return d


# ══════════════════════════════════════════════════════════
# 用户 CRUD
# ══════════════════════════════════════════════════════════

def list_users() -> list[dict]:
    conn = get_conn()
    with conn.cursor() as cur:
        # 不返回 password 字段，避免哈希值外泄到前端
        cur.execute("SELECT id, username, role, name, created_at FROM users ORDER BY created_at")
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def create_user(username: str, hashed_password: str, role: str, name: str) -> str:
    """
    注意：调用方负责在传入前完成 hash_password()，store 只存哈希。
    """
    user_id = str(uuid.uuid4())
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO users (id, username, password, role, name, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, username, hashed_password, role, name, _now()))
    conn.commit()
    return user_id


def delete_user(user_id: str) -> bool:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
        affected = cur.rowcount
    conn.commit()
    return affected > 0


def get_user_by_username(username: str) -> dict | None:
    """返回含 password（哈希）字段的完整记录，仅供登录验证使用"""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_dict(row)


# ══════════════════════════════════════════════════════════
# 统计
# ══════════════════════════════════════════════════════════

def get_stats() -> dict:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM documents")
        total_docs = cur.fetchone()["cnt"]

        cur.execute("SELECT COALESCE(SUM(node_count), 0) AS total FROM documents")
        total_nodes = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        total_users = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM documents WHERE status='indexed'")
        indexed = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM documents WHERE status IN ('pending','indexing')")
        pending = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM documents WHERE status='error'")
        errors = cur.fetchone()["cnt"]

    return {
        "total_docs":  total_docs,
        "total_nodes": int(total_nodes),
        "total_users": total_users,
        "indexed":     indexed,
        "pending":     pending,
        "errors":      errors,
    }