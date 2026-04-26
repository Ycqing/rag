"""
services/ingest.py  —  文档解析 + 向量入库服务

供 routers/docs.py 异步调用，入库完成后更新 SQLite 状态
"""

import sys
import asyncio
from pathlib import Path

# 把上级目录加入 path，复用已有的 doc_loader / config
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from doc_loader import parse_file, PARSERS
from llama_index.core import Document, VectorStoreIndex, StorageContext
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.dashscope import DashScopeEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
import config
from admin.services.store import update_document_status

UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


def _get_vector_store():
    client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY or None)
    return QdrantVectorStore(client=client, collection_name=config.COLLECTION_NAME)


async def ingest_document(doc_id: str, file_path: Path, roles: list[str]):
    """
    异步入库任务，流程：
      1. 解析文件内容
      2. 构造带 roles 标签的 Document
      3. 分片
      4. 写入 Qdrant
      5. 更新 SQLite 状态
    """
    update_document_status(doc_id, "indexing")
    try:
        # 1. 解析文件
        text = await asyncio.to_thread(parse_file, file_path)
        if not text.strip():
            raise ValueError("文件解析后内容为空，可能是扫描版 PDF 或格式不支持")

        # 2. 构造 Document
        doc = Document(
            text=text,
            metadata={
                "doc_id":   doc_id,
                "roles":    roles,
                "source":   file_path.name,
                "doc_type": file_path.suffix.lstrip(".").upper(),
            }
        )

        # 3. 分片
        splitter = SentenceSplitter(chunk_size=500, chunk_overlap=80)
        nodes = splitter.get_nodes_from_documents([doc])

        # 排除权限字段参与向量化
        for node in nodes:
            node.excluded_embed_metadata_keys = ["roles", "doc_id"]

        # 4. 写入向量库
        embed_model = DashScopeEmbedding(
            model_name=config.EMBED_MODEL_NAME,
            api_key=config.DASHSCOPE_API_KEY,
        )
        vector_store = _get_vector_store()
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        await asyncio.to_thread(
            lambda: VectorStoreIndex(
                nodes=nodes,
                storage_context=storage_context,
                embed_model=embed_model,
            )
        )

        # 5. 更新状态
        update_document_status(doc_id, "indexed", node_count=len(nodes))
        return len(nodes)

    except Exception as e:
        update_document_status(doc_id, "error", error_msg=str(e))
        raise


async def delete_document_vectors(doc_id: str):
    """
    从 Qdrant 中删除该文档的所有向量节点
    通过 doc_id 过滤删除
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY or None)
    await asyncio.to_thread(
        client.delete,
        collection_name=config.COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        )
    )
