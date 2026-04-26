"""
mcp_server/server.py  —  星辰科技知识库 MCP Server

把知识库工具暴露给任何支持 MCP 协议的客户端：
  - Claude Desktop
  - Cursor / Continue
  - 任何用 mcp Python SDK 的程序

暴露的工具：
  - kb_search      按角色权限检索内部知识库
  - db_query       查询员工/部门/假期数据
  - get_employee   按姓名快速查员工信息
  - calculator     数学计算
  - get_datetime   时间信息

支持两种传输方式：
  1. stdio  —— 本地进程通信，Claude Desktop 用这种
  2. SSE   ——  HTTP 网络传输，远程客户端用这种

安装依赖：
  pip install fastmcp mcp

启动（stdio 模式，供 Claude Desktop 配置）：
  cd rag_rbac
  python -m mcp_server.server

启动（SSE 模式，供网络客户端连接）：
  cd rag_rbac
  python -m mcp_server.server --transport sse --port 8003

Claude Desktop 配置（~/.config/claude/claude_desktop_config.json）：
  {
    "mcpServers": {
      "startech-kb": {
        "command": "python",
        "args": ["-m", "mcp_server.server"],
        "cwd": "/path/to/rag_rbac",
        "env": {
          "DASHSCOPE_API_KEY": "sk-xxx",
          "KB_ACCESS_TOKEN": "your-token",
          "KB_DEFAULT_ROLE": "all"
        }
      }
    }
  }
"""

import sys
import os
import asyncio
import argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── 权限 Token 校验 ───────────────────────────────────────────────────────────
# 客户端需要在环境变量 KB_ACCESS_TOKEN 中提供 Token
# 不配置则跳过校验（仅限本地 stdio 模式使用）
ACCESS_TOKEN   = os.getenv("KB_ACCESS_TOKEN", "")
DEFAULT_ROLE   = os.getenv("KB_DEFAULT_ROLE", "all")   # 客户端默认角色
SERVER_VERSION = "1.0.0"


# ── 导入现有工具 handler（复用，不重复写逻辑）────────────────────────────────
from chat.tools.definitions import (
    handle_kb_search,
    handle_db_query,
    handle_calculator,
    handle_datetime,
    _init_demo_db,
)


# ══════════════════════════════════════════════════════════════════════════════
# FastMCP 版本（推荐，代码最简洁）
# ══════════════════════════════════════════════════════════════════════════════

def create_fastmcp_server():
    """使用 fastmcp 创建 MCP Server，装饰器风格注册工具"""
    from fastmcp import FastMCP

    mcp = FastMCP(
        name="startech-kb",
        version=SERVER_VERSION,
        #description="星辰科技内部知识库 MCP Server，提供文档检索、员工数据查询等能力",
    )

    # ── 工具 1：知识库检索 ────────────────────────────────────────────────────
    @mcp.tool()
    async def kb_search(
        query: str,
        role: str = DEFAULT_ROLE,
    ) -> str:
        """
        搜索星辰科技内部知识库。

        Args:
            query: 搜索关键词，如「年假规定」「报销发票要求」「代码提交规范」
            role:  访问角色，决定可见文档范围。可选：all / hr / dev / admin / manager
                   默认为 all（全员可见文档）
        """
        _check_token()
        return await asyncio.to_thread(handle_kb_search, query, role)

    # ── 工具 2：数据库查询 ────────────────────────────────────────────────────
    @mcp.tool()
    async def db_query(
        sql: str,
        role: str = DEFAULT_ROLE,
    ) -> str:
        """
        查询公司员工数据库（只读）。

        可查询的表：
          - employees(id, name, dept, level, join_date, status)
          - departments(id, name, manager_name, headcount)
          - leave_balance(employee_id, annual_days, used_days, remaining_days, year)

        Args:
            sql:  SELECT 语句，如 "SELECT name, dept FROM employees WHERE dept='研发部'"
            role: 访问角色，all 角色无权查询此接口
        """
        _check_token()
        if role == "all":
            return "权限不足：需要 hr 或以上角色才能查询员工数据库"
        _init_demo_db()
        return await asyncio.to_thread(handle_db_query, sql, role)

    # ── 工具 3：快速查员工 ────────────────────────────────────────────────────
    @mcp.tool()
    async def get_employee(
        name: str,
        role: str = DEFAULT_ROLE,
    ) -> str:
        """
        按姓名查询员工基本信息（部门、职级、入职时间、年假余额）。

        Args:
            name: 员工姓名，支持模糊匹配，如「张」可查所有张姓员工
            role: 访问角色
        """
        _check_token()
        if role == "all":
            return "权限不足：需要 hr 或以上角色"
        sql = f"""
            SELECT e.name, e.dept, e.level, e.join_date, e.status,
                   lb.annual_days, lb.used_days, lb.remaining_days
            FROM employees e
            LEFT JOIN leave_balance lb ON e.id = lb.employee_id AND lb.year = 2025
            WHERE e.name LIKE '%{name}%'
            LIMIT 5
        """
        _init_demo_db()
        return await asyncio.to_thread(handle_db_query, sql, role)

    # ── 工具 4：计算器 ────────────────────────────────────────────────────────
    @mcp.tool()
    async def calculator(expression: str) -> str:
        """
        执行数学计算，支持四则运算和常用数学函数。

        Args:
            expression: 数学表达式，如 "28000 * 0.8" 或 "(3000 + 500) * 12"
        """
        return await asyncio.to_thread(handle_calculator, expression)

    # ── 工具 5：日期时间 ──────────────────────────────────────────────────────
    @mcp.tool()
    async def get_datetime(
        query_type: str = "now",
    ) -> str:
        """
        获取时间相关信息。

        Args:
            query_type: 查询类型
              - now                  当前时间
              - days_until_year_end  距年底天数
              - workdays_this_month  本月工作日数
        """
        return await asyncio.to_thread(handle_datetime, query_type)

    # ── Resource：知识库元信息 ────────────────────────────────────────────────
    @mcp.resource("kb://info")
    async def kb_info() -> str:
        """返回知识库基本信息，帮助客户端了解数据范围"""
        return """
星辰科技知识库 MCP Server
版本：1.0.0

可检索的文档类型：
  - all   (全员)：员工手册、考勤制度、报销流程
  - hr    (HR专属)：薪资结构、绩效考核、离职流程
  - dev   (研发专属)：代码规范、架构文档、发布流程
  - admin (管理层)：经营数据、战略文档

员工数据库包含：员工信息、部门信息、假期余额
数据截止：2025年Q1
        """.strip()

    return mcp


# ══════════════════════════════════════════════════════════════════════════════
# 原生 mcp SDK 版本（fastmcp 不可用时的备选）
# ══════════════════════════════════════════════════════════════════════════════

def create_native_mcp_server():
    """使用官方 mcp SDK 创建 Server（更底层，fastmcp 不可用时使用）"""
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    import mcp.types as types

    server = Server("startech-kb")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="kb_search",
                description="搜索星辰科技内部知识库，包含员工手册、HR政策、研发规范等",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "role":  {"type": "string", "description": "访问角色：all/hr/dev/admin",
                                  "default": DEFAULT_ROLE},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="db_query",
                description="查询公司员工数据库，支持 SELECT 语句",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql":  {"type": "string", "description": "SELECT 语句"},
                        "role": {"type": "string", "default": DEFAULT_ROLE},
                    },
                    "required": ["sql"],
                },
            ),
            types.Tool(
                name="calculator",
                description="数学计算",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "数学表达式"},
                    },
                    "required": ["expression"],
                },
            ),
            types.Tool(
                name="get_datetime",
                description="获取当前时间或日期计算",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query_type": {
                            "type": "string",
                            "enum": ["now", "days_until_year_end", "workdays_this_month"],
                        },
                    },
                    "required": ["query_type"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        _check_token()
        role = arguments.get("role", DEFAULT_ROLE)

        if name == "kb_search":
            result = await asyncio.to_thread(handle_kb_search, arguments["query"], role)
        elif name == "db_query":
            result = await asyncio.to_thread(handle_db_query, arguments["sql"], role)
        elif name == "calculator":
            result = await asyncio.to_thread(handle_calculator, arguments["expression"])
        elif name == "get_datetime":
            result = await asyncio.to_thread(handle_datetime, arguments.get("query_type", "now"))
        else:
            result = f"未知工具：{name}"

        return [types.TextContent(type="text", text=result)]

    return server


# ── 权限校验 ──────────────────────────────────────────────────────────────────
def _check_token():
    """若配置了 ACCESS_TOKEN，校验客户端提供的 token"""
    if not ACCESS_TOKEN:
        return   # 未配置则跳过（本地 stdio 模式）
    client_token = os.getenv("KB_CLIENT_TOKEN", "")
    if client_token != ACCESS_TOKEN:
        raise PermissionError("无效的访问 Token，请在环境变量 KB_CLIENT_TOKEN 中提供正确的 Token")


# ── 启动入口 ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="星辰科技知识库 MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio",
                        help="传输方式：stdio（本地）或 sse（网络）")
    parser.add_argument("--port", type=int, default=8003, help="SSE 模式端口")
    parser.add_argument("--host", default="0.0.0.0", help="SSE 模式监听地址")
    parser.add_argument("--backend", choices=["fastmcp", "native"], default="fastmcp",
                        help="使用 fastmcp 或原生 mcp SDK")
    args = parser.parse_args()

    if args.backend == "fastmcp":
        try:
            mcp = create_fastmcp_server()
            if args.transport == "stdio":
                print("启动 stdio 模式（供 Claude Desktop 使用）", file=sys.stderr)
                mcp.run(transport="stdio")
            else:
                print(f"启动 SSE 模式：http://{args.host}:{args.port}", file=sys.stderr)
                mcp.run(transport="sse", host=args.host, port=args.port)
        except ImportError:
            print("fastmcp 未安装，切换到原生 mcp SDK", file=sys.stderr)
            args.backend = "native"

    if args.backend == "native":
        from mcp.server.stdio import stdio_server
        from mcp.server.sse import SseServerTransport
        server = create_native_mcp_server()

        if args.transport == "stdio":
            async def _run():
                async with stdio_server() as (r, w):
                    await server.run(r, w, server.create_initialization_options())
            asyncio.run(_run())
        else:
            # SSE 需要配合 starlette/uvicorn
            from starlette.applications import Starlette
            from starlette.routing import Route
            import uvicorn

            sse_transport = SseServerTransport("/messages")

            async def handle_sse(request):
                async with sse_transport.connect_sse(
                    request.scope, request.receive, request._send
                ) as (r, w):
                    await server.run(r, w, server.create_initialization_options())

            app = Starlette(routes=[
                Route("/sse",      handle_sse),
                Route("/messages", sse_transport.handle_post_message, methods=["POST"]),
            ])
            uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
