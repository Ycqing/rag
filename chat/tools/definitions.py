"""
chat/tools/definitions.py  —  所有工具的定义

每个工具包含两部分：
  1. schema  —— 告诉 LLM 这个工具的名称、描述、参数（OpenAI function calling 格式）
  2. handler —— 实际执行逻辑，返回字符串结果

新增工具只需在 ALL_TOOLS 末尾加一项，Agent 自动感知。
"""

import json
import math
import datetime
import sqlite3
import sys
import asyncio
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

# ══════════════════════════════════════════════════════════════════════════════
# 工具 1：知识库检索
# ══════════════════════════════════════════════════════════════════════════════

def handle_kb_search(query: str, user_role: str) -> str:
    """按用户角色权限检索内部知识库"""
    try:
        from llama_index.core import VectorStoreIndex
        from llama_index.core.vector_stores import (
            MetadataFilter, MetadataFilters, FilterOperator, FilterCondition
        )
        from llama_index.embeddings.dashscope import DashScopeEmbedding
        from llama_index.vector_stores.qdrant import QdrantVectorStore
        from qdrant_client import QdrantClient
        import config

        accessible = config.get_accessible_roles(user_role)
        filters = MetadataFilters(
            filters=[
                MetadataFilter(key="roles", value=r, operator=FilterOperator.CONTAINS)
                for r in accessible
            ],
            condition=FilterCondition.OR,
        )
        client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY or None)
        embed  = DashScopeEmbedding(model_name=config.EMBED_MODEL_NAME, api_key=config.DASHSCOPE_API_KEY)
        vs     = QdrantVectorStore(client=client, collection_name=config.COLLECTION_NAME)
        index  = VectorStoreIndex.from_vector_store(vector_store=vs, embed_model=embed)
        nodes  = index.as_retriever(similarity_top_k=5, filters=filters).retrieve(query)

        if not nodes:
            return "知识库中未找到相关内容。"

        results = []
        for i, n in enumerate(nodes, 1):
            src = n.metadata.get("source", "未知来源")
            results.append(f"[{i}] 来源：{src}\n{n.text.strip()}")
        return "\n\n".join(results)

    except Exception as e:
        return f"知识库检索失败：{e}"


KB_SEARCH_SCHEMA = {
    "name": "kb_search",
    "description": (
        "搜索公司内部知识库，包含员工手册、HR政策、薪资制度、研发规范、报销流程等内部文档。"
        "当用户询问公司规定、内部流程、制度政策时，优先使用此工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词或问题，尽量简洁精确，如「年假天数」「报销发票要求」"
            }
        },
        "required": ["query"]
    }
}


# ══════════════════════════════════════════════════════════════════════════════
# 工具 2：网页搜索
# ══════════════════════════════════════════════════════════════════════════════

def handle_web_search(
    query: str,
    max_results: int = 5,
    time_filter: str = "y",
    search_type: str = "web"
) -> str:
    """
    调用 DuckDuckGo 搜索实时信息（网页或新闻）

    Args:
        query: 搜索关键词
        max_results: 返回结果数量（默认5，最大10）
        time_filter: 时效过滤，支持 'd'(一天), 'w'(一周), 'm'(一月), 'y'(一年)，None表示不限
        search_type: 搜索类型，"web" 为普通网页，"news" 为新闻

    Returns:
        格式化的搜索结果字符串
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return "❌ 网页搜索不可用，请安装依赖：pip install ddgs"

    results = []
    try:
        with DDGS() as ddgs:
            if search_type == "news":
                # ----- 新闻搜索（更重视时效）-----
                raw_results = _ddgs_retry(ddgs.news,
                    query,
                    max_results=min(max_results, 10),
                    region="cn-zh",
                    timelimit=time_filter or None,
                )

                for r in raw_results:
                    results.append(
                        f"📰 {r['title']}\n"
                        f"📅 {r['date']}  {r.get('source', '未知来源')}\n"
                        f"📄 {r['body']}\n"
                        f"🔗 {r['url']}"
                    )
                # 在 results 最后附加一条系统注记
                results.append(
                    f"⚠️ 注：以上搜索结果中的日期均为新闻发布时的真实日期。当前真实日期是 {datetime.now().strftime('%Y-%m-%d')}，"
                    "所有日期在当前或以前的均为正常有效的实时信息。"
                )
            else:
                # ----- 普通网页搜索（可加入时效参数）-----
                # 注意：ddgs.text() 不直接支持 timelimit，但可以用 fresh 参数（需要确认）
                # 为简单起见，这里通过 region 和排序来优化，不加额外时效（可使用新闻搜索代替）
                raw_results = _ddgs_retry(ddgs.text,
                    query,
                    max_results=min(max_results, 10),
                    region="cn-zh"  # 中文优先，可根据需要调整
                )
                for r in raw_results:
                    results.append(
                        f"🔍 {r['title']}\n"
                        f"📝 {r['body']}\n"
                        f"🔗 {r['href']}"
                    )

        if not results:
            return "⚠️ 没有找到相关结果，请尝试修改关键词。"

        return "\n\n---\n\n".join(results)

    except Exception as e:
        return f"❌ 搜索失败：{str(e)}"

WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": (
        "搜索互联网上的实时信息，支持普通网页和新闻两种模式。"
        "当用户询问最新新闻、政策、行业动态、技术问题等时优先使用。"
        "对于新闻类问题（如'今天有什么新闻'），请设置 search_type='news' 和 time_filter='d'。"
        "对于普通知识或教程类问题，使用 search_type='web'。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，如「2026年新能源汽车购置税政策」「Python异步编程最佳实践」「今天新闻」"
            },
            "max_results": {
                "type": "integer",
                "description": "返回结果数量，默认5，最大不超过10",
                "default": 5
            },
            "time_filter": {
                "type": "string",
                "enum": ["d", "w", "m", "y"],
                "description": "时效过滤：d=一天内, w=一周内, m=一月内, y=一年内。仅 search_type='news' 时生效",
                "default": None
            },
            "search_type": {
                "type": "string",
                "enum": ["web", "news"],
                "description": "搜索类型：web=普通网页，news=新闻（更重视时效和来源）",
                "default": "web"
            }
        },
        "required": ["query"]
    }
}

import time

def _ddgs_retry(fn, *args, retries=3, **kwargs):
    """DDG 限流时自动重试"""
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))  # 递增等待：1.5s、3s
    return []

# ══════════════════════════════════════════════════════════════════════════════
# 工具 3：数据库查询（Text-to-SQL）
# ══════════════════════════════════════════════════════════════════════════════

# 演示数据库路径，包含员工/部门/假期余额等表
DEMO_DB = ROOT / "db" / "demo.db"

# 暴露给 LLM 的数据库 Schema 描述（不暴露敏感字段）
DB_SCHEMA_DESC = """
可查询的数据库表结构：

employees（员工信息）
  - id: 员工ID
  - name: 姓名
  - dept: 部门（研发部/HR部/市场部/销售部/管理层）
  - level: 职级（P3-P7, M1-M3）
  - join_date: 入职日期
  - status: 状态（在职/离职/试用期）

leave_balance（假期余额）
  - employee_id: 员工ID
  - annual_days: 年假总天数
  - used_days: 已使用天数
  - remaining_days: 剩余天数
  - year: 年份

departments（部门信息）
  - id: 部门ID
  - name: 部门名称
  - manager_name: 部门负责人
  - headcount: 人数

注意：不包含薪资字段，薪资信息属于敏感数据不可查询。
"""

def _init_demo_db():
    """初始化演示数据库，首次运行时创建测试数据"""
    if DEMO_DB.exists():
        return
    DEMO_DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DEMO_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS employees (
            id TEXT PRIMARY KEY, name TEXT, dept TEXT,
            level TEXT, join_date TEXT, status TEXT
        );
        CREATE TABLE IF NOT EXISTS departments (
            id TEXT PRIMARY KEY, name TEXT, manager_name TEXT, headcount INTEGER
        );
        CREATE TABLE IF NOT EXISTS leave_balance (
            employee_id TEXT, annual_days INTEGER,
            used_days INTEGER, remaining_days INTEGER, year INTEGER
        );

        INSERT OR IGNORE INTO departments VALUES
          ('D1','研发部','张伟',32),('D2','HR部','李婷',8),
          ('D3','市场部','王磊',15),('D4','销售部','陈静',20),
          ('D5','管理层','赵总',5);

        INSERT OR IGNORE INTO employees VALUES
          ('E001','张三','研发部','P5','2022-03-15','在职'),
          ('E002','李四','研发部','P6','2020-07-01','在职'),
          ('E003','王五','HR部','P4','2023-01-10','在职'),
          ('E004','赵六','市场部','P5','2021-11-20','在职'),
          ('E005','钱七','销售部','M1','2019-05-08','在职'),
          ('E006','孙八','研发部','P4','2024-02-01','试用期'),
          ('E007','周九','HR部','P3','2024-06-15','试用期'),
          ('E008','吴十','管理层','M2','2018-03-01','在职'),
          ('E009','郑十一','研发部','P7','2017-09-01','在职'),
          ('E010','王十二','市场部','P4','2023-08-20','在职');
charlie
        INSERT OR IGNORE INTO leave_balance VALUES
          ('E001',10,3,7,2025),('E002',15,8,7,2025),
          ('E003',5,1,4,2025), ('E004',10,5,5,2025),
          ('E005',15,10,5,2025),('E006',0,0,0,2025),
          ('E007',0,0,0,2025), ('E008',15,12,3,2025),
          ('E009',15,6,9,2025),('E010',5,2,3,2025);
    """)
    conn.commit()
    conn.close()


def handle_db_query(sql: str, user_role: str) -> str:
    """
    执行 SQL 查询并返回结果。
    安全限制：只允许 SELECT，且 HR/admin 才能查薪资相关（这里演示库无薪资字段）。
    """
    _init_demo_db()

    # 安全校验：只允许 SELECT
    sql_clean = sql.strip().upper()
    if not sql_clean.startswith("SELECT"):
        return "出于安全考虑，只允许执行 SELECT 查询。"

    # 禁止危险关键词
    forbidden = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE"]
    if any(kw in sql_clean for kw in forbidden):
        return "不允许执行数据修改操作。"

    try:
        conn = sqlite3.connect(DEMO_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql)
        rows = cur.fetchmany(20)  # 最多返回 20 行
        conn.close()

        if not rows:
            return "查询结果为空。"

        headers = rows[0].keys()
        lines = [" | ".join(headers)]
        lines.append("-" * (len(lines[0])))
        for row in rows:
            lines.append(" | ".join(str(v) if v is not None else "—" for v in row))

        total = len(rows)
        result = "\n".join(lines)
        if total == 20:
            result += "\n（结果已截断，最多显示 20 行）"
        return result

    except Exception as e:
        return f"SQL 执行错误：{e}\n请检查 SQL 语法是否正确。"


DB_QUERY_SCHEMA = {
    "name": "db_query",
    "description": (
        "查询公司业务数据库，可查员工信息（姓名、部门、职级、入职时间）、"
        "假期余额、部门人数等。当用户询问具体的员工数据或统计信息时使用。\n\n"
        f"数据库结构：\n{DB_SCHEMA_DESC}"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "标准 SQLite SELECT 语句，如：SELECT name, dept FROM employees WHERE name='张三'"
            }
        },
        "required": ["sql"]
    }
}


# ══════════════════════════════════════════════════════════════════════════════
# 工具 4：计算器
# ══════════════════════════════════════════════════════════════════════════════

def handle_calculator(expression: str) -> str:
    """安全地执行数学表达式计算"""
    # 白名单：只允许数字和安全的数学函数
    allowed = set("0123456789+-*/().,% \t")
    allowed_words = {"abs", "round", "max", "min", "sum", "pow", "sqrt"}
    safe_globals = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    safe_globals.update({"__builtins__": {}, "abs": abs, "round": round,
                          "max": max, "min": min, "sum": sum, "pow": pow})
    try:
        result = eval(expression, safe_globals, {})  # noqa
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算失败：{e}"


CALCULATOR_SCHEMA = {
    "name": "calculator",
    "description": (
        "执行数学计算，支持四则运算、百分比、幂运算等。"
        "当用户需要计算薪资税后金额、报销金额汇总、天数计算时使用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "数学表达式字符串，如 '28000 * 0.8' 或 '(3000 + 500) * 12'"
            }
        },
        "required": ["expression"]
    }
}


# ══════════════════════════════════════════════════════════════════════════════
# 工具 5：日期时间
# ══════════════════════════════════════════════════════════════════════════════

def handle_datetime(query_type: str, date_str: str = None) -> str:
    """返回时间相关信息"""
    now = datetime.datetime.now()
    if query_type == "now":
        return f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M:%S')}，星期{['一','二','三','四','五','六','日'][now.weekday()]}"
    elif query_type == "days_until_year_end":
        year_end = datetime.datetime(now.year, 12, 31)
        days = (year_end - now).days
        return f"距离今年年底（{now.year}年12月31日）还有 {days} 天"
    elif query_type == "workdays_this_month":
        import calendar
        _, last_day = calendar.monthrange(now.year, now.month)
        workdays = sum(
            1 for d in range(1, last_day + 1)
            if datetime.datetime(now.year, now.month, d).weekday() < 5
        )
        return f"{now.year}年{now.month}月共有 {workdays} 个工作日（不含法定节假日）"
    else:
        return f"当前日期：{now.strftime('%Y-%m-%d')}，{now.strftime('%A')}"


DATETIME_SCHEMA = {
    "name": "get_datetime",
    "description": "获取当前时间、计算日期差、查询本月工作日数等时间相关信息。",
    "parameters": {
        "type": "object",
        "properties": {
            "query_type": {
                "type": "string",
                "enum": ["now", "days_until_year_end", "workdays_this_month"],
                "description": "now=当前时间，days_until_year_end=距年底天数，workdays_this_month=本月工作日数"
            }
        },
        "required": ["query_type"]
    }
}


# ══════════════════════════════════════════════════════════════════════════════
# 工具注册表：新增工具只需在这里加一项
# ══════════════════════════════════════════════════════════════════════════════

def get_all_tools(user_role: str) -> list[dict]:
    """
    返回当前角色可用的工具 schema 列表（传给 LLM）。
    自动合并本地工具 + 所有已连接的 MCP 外部工具。
    """
    tools = [
        KB_SEARCH_SCHEMA,
        WEB_SEARCH_SCHEMA,
        DB_QUERY_SCHEMA,
        CALCULATOR_SCHEMA,
        DATETIME_SCHEMA,
    ]
    # 数据库查询只有 hr 及以上角色可用
    if user_role == "all":
        tools = [t for t in tools if t["name"] != "db_query"]

    # ── 注入 MCP 外部工具 ──────────────────────────────────────────────────────
    # mcp_manager 在应用启动时已初始化，这里直接拿工具列表
    try:
        from chat.tools.mcp_client import mcp_manager
        mcp_tools = mcp_manager.get_all_tool_schemas()
        tools.extend(mcp_tools)
    except Exception:
        pass   # MCP 不可用时静默降级，不影响本地工具

    return tools


def dispatch_tool(name: str, args: dict, user_role: str) -> str:
    """
    本地同步工具分发。MCP 工具（mcp__ 前缀）已在 agent.py 的
    _call_mcp_tool 中直接异步执行，不会到达这里。
    """

    # ── 本地工具路由 ───────────────────────────────────────────────────────────
    if name == "kb_search":
        return handle_kb_search(args["query"], user_role)
    elif name == "web_search":
        return handle_web_search(
			    query=args.get("query"),
			    max_results=args.get("max_results", 5),
			    time_filter=args.get("time_filter", "y"),
			    search_type=args.get("search_type", "web")
			)
    elif name == "db_query":
        if user_role == "all":
            return "您没有权限查询员工数据库。"
        return handle_db_query(args["sql"], user_role)
    elif name == "calculator":
        return handle_calculator(args["expression"])
    elif name == "get_datetime":
        return handle_datetime(args["query_type"])
    else:
        return f"未知工具：{name}"
