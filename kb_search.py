"""
02_search.py  —  按角色检索，核心：构造 roles 过滤条件

运行方式：python 02_search.py
"""

from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.vector_stores import (
    MetadataFilter, MetadataFilters, FilterOperator, FilterCondition
)
from llama_index.embeddings.dashscope import DashScopeEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
import config


def get_retriever(user_role: str, top_k: int = 5):
    """
    根据用户角色构造带权限过滤的检索器。

    过滤逻辑：
      文档的 roles 字段必须包含用户有权限访问的某个角色标签。
      例如：用户是 hr，可以访问标记为 "hr" 或 "all" 的文档。
    """
    accessible = config.get_accessible_roles(user_role)
    print(f"用户角色: {user_role}，可访问标签: {accessible}")

    # 构造过滤器：roles 字段包含任一可访问标签（OR 关系）
    filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key="roles",
                value=role,
                operator=FilterOperator.CONTAINS,
            )
            for role in accessible
        ],
        condition=FilterCondition.OR,   # 满足任一即可
    )

    client = QdrantClient(
        url=config.QDRANT_URL,
        api_key=config.QDRANT_API_KEY or None,
    )
    embed_model = DashScopeEmbedding(
        model_name=config.EMBED_MODEL_NAME,
        api_key=config.DASHSCOPE_API_KEY,
    )
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=config.COLLECTION_NAME,
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        embed_model=embed_model,
    )

    retriever = index.as_retriever(
        similarity_top_k=top_k,
        filters=filters,
    )
    return retriever


def search(query: str, user_role: str):
    """执行带权限的检索，返回节点列表"""
    retriever = get_retriever(user_role)
    nodes = retriever.retrieve(query)
    return nodes


def print_results(query: str, user_role: str):
    print(f"\n{'='*60}")
    print(f"问题：{query}")
    print(f"角色：{user_role}")
    print(f"{'='*60}")
    nodes = search(query, user_role)
    if not nodes:
        print("  未检索到任何内容（权限不足或无相关文档）")
    for i, node in enumerate(nodes, 1):
        print(f"\n  [{i}] 来源: {node.metadata.get('source', '未知')}")
        print(f"       角色标签: {node.metadata.get('roles', [])}")
        print(f"       相似度: {node.score:.4f}")
        print(f"       内容摘要: {node.text[:80].strip()}...")


if __name__ == "__main__":
    # ── 验证场景 1：普通员工查薪资（应当被拦截）──────────────────────────
    print_results("P5 级别的薪资范围是多少", user_role="all")

    # ── 验证场景 2：HR 查薪资（应当返回结果）─────────────────────────────
    print_results("P5 级别的薪资范围是多少", user_role="hr")

    # ── 验证场景 3：普通员工查请假制度（应当返回结果）────────────────────
    print_results("年假没用完可以留到明年吗", user_role="all")

    # ── 验证场景 4：研发查架构文档（应当返回结果）────────────────────────
    print_results("系统的 SLA 要求是多少", user_role="dev")

    # ── 验证场景 5：HR 查研发规范（应当被拦截）───────────────────────────
    print_results("PR 合并需要几个 reviewer", user_role="hr")

    # ── 验证场景 6：admin 查所有内容（应全部返回）────────────────────────
    print_results("Q1 营收数据", user_role="admin")
