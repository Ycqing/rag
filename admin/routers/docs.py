"""
routers/docs.py  —  文档管理 API

POST   /api/docs/upload     上传文档并触发异步入库
GET    /api/docs            获取文档列表
DELETE /api/docs/{doc_id}   删除文档（同时清除向量）
"""

import json
import asyncio
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks

from admin.services import store, ingest

router = APIRouter(prefix="/api/docs", tags=["documents"])

UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".md", ".txt", ".xlsx", ".xls"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


@router.get("")
def list_docs():
    """获取所有文档列表"""
    return store.list_documents()


@router.post("/upload")
async def upload_doc(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    roles: str = Form(...),         # JSON 字符串，如 '["hr","admin"]'
):
    """
    上传文档并触发后台异步入库。
    立即返回 doc_id，前端轮询 /api/docs 查看状态变化。
    """
    # 校验扩展名
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式: {suffix}")

    # 解析 roles
    try:
        roles_list = json.loads(roles)
        assert isinstance(roles_list, list) and len(roles_list) > 0
    except Exception:
        raise HTTPException(400, "roles 格式错误，应为 JSON 数组，如 [\"hr\",\"admin\"]")

    # 读取文件内容
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"文件超过 50MB 限制")

    # 保存到磁盘
    save_path = UPLOAD_DIR / file.filename
    save_path.write_bytes(content)

    # 写入 SQLite，状态为 pending
    doc_id = store.create_document(
        filename=file.filename,
        doc_type=suffix.lstrip(".").upper(),
        roles=roles_list,
        file_size=len(content),
    )

    # 后台异步入库，不阻塞 HTTP 响应
    background_tasks.add_task(ingest.ingest_document, doc_id, save_path, roles_list)

    return {"doc_id": doc_id, "status": "pending", "message": "文件已上传，正在后台索引"}


@router.delete("/{doc_id}")
async def delete_doc(doc_id: str):
    """删除文档：同时删除 SQLite 记录和 Qdrant 向量"""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")

    # 删除向量
    try:
        await ingest.delete_document_vectors(doc_id)
    except Exception as e:
        raise HTTPException(500, f"删除向量失败: {e}")

    # 删除文件
    file_path = UPLOAD_DIR / doc["filename"]
    if file_path.exists():
        file_path.unlink()

    # 删除 SQLite 记录
    store.delete_document(doc_id)
    return {"message": "删除成功"}


@router.get("/stats")
def get_stats():
    """获取统计数据"""
    return store.get_stats()
