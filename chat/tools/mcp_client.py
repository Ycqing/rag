"""
chat/tools/mcp_client.py  —  MCP Client 封装

功能：
  1. 连接外部 MCP Server（stdio 或 SSE）
  2. 动态发现工具列表
  3. 把外部工具注入到 Agent 的工具列表中
  4. 执行工具调用并返回结果

使用方式：
  # 在 config.py 中配置要连接的 MCP Server
  MCP_SERVERS = [
      {
          "name": "filesystem",
          "transport": "stdio",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      },
      {
          "name": "github",
          "transport": "sse",
          "url": "http://localhost:8004/sse",
      },
  ]
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════════════════════════
# MCP Server 配置（在这里或 config.py 里维护）
# ══════════════════════════════════════════════════════════════════════════════

# 示例：连接本地文件系统 MCP Server 和自身的知识库 MCP Server
EXAMPLE_SERVERS = [
    # 连接自己的知识库 MCP Server（SSE 模式，需先启动 mcp_server/server.py --transport sse）
    {
        "name":      "startech-kb",
        "label":     "星辰知识库（MCP）",
        "transport": "sse",
        "url":       "http://localhost:8003/sse",
        "enabled":   True,   # 改为 True 启用
    },
    # 连接官方文件系统 MCP Server（需要 npx）
    {
        "name":      "filesystem",
        "label":     "本地文件系统",
        "transport": "stdio",
        "command":   "npx",
        "args":      ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "enabled":   False,
    },
    # 自定义示例：连接公司内部其他 MCP Server
    {
        "name":      "company-erp",
        "label":     "ERP 系统",
        "transport": "sse",
        "url":       "http://erp.startech-inc.com/mcp/sse",
        "enabled":   False,
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# MCP Client 会话管理
# ══════════════════════════════════════════════════════════════════════════════

class MCPClientSession:
    """
    单个 MCP Server 的连接会话。
    支持 stdio（子进程）和 SSE（HTTP）两种传输方式。
    """

    def __init__(self, config: dict):
        self.config    = config
        self.name      = config["name"]
        self.label     = config.get("label", config["name"])
        self.transport = config["transport"]
        self._session  = None
        self._tools    = []     # 缓存该 server 的工具列表

    async def connect(self):
        """建立连接，获取工具列表"""
        try:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client, StdioServerParameters
            from mcp.client.sse   import sse_client

            if self.transport == "stdio":
                # ✅ 必须用 StdioServerParameters，不能传 dict
                params = StdioServerParameters(
                    command=self.config["command"],
                    args=self.config.get("args", []),
                    env=self.config.get("env"),
                )
                self._transport_cm = stdio_client(params)

            elif self.transport == "sse":
                self._transport_cm = sse_client(self.config["url"])

            else:
                raise ValueError(f"不支持的传输方式：{self.transport}")

            # 进入传输层 context manager，拿到读写流
            self._read, self._write = await self._transport_cm.__aenter__()

            # 建立 ClientSession 并初始化
            self._session = ClientSession(self._read, self._write)
            await self._session.__aenter__()
            await self._session.initialize()

            # 获取工具列表并缓存
            result      = await self._session.list_tools()
            self._tools = result.tools
            print(f"[MCP] 已连接 {self.name}，发现 {len(self._tools)} 个工具", file=sys.stderr)
            return True

        except ImportError:
            print("[MCP] mcp SDK 未安装，请运行：pip install mcp", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[MCP] 连接 {self.name} 失败：{e}", file=sys.stderr)
            return False

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用工具，返回字符串结果"""
        if not self._session:
            return f"MCP Server {self.name} 未连接"
        try:
            result = await self._session.call_tool(tool_name, arguments)
            # 把所有 content 块拼成字符串
            parts = []
            for content in result.content:
                if hasattr(content, "text"):
                    parts.append(content.text)
                elif hasattr(content, "data"):
                    parts.append(str(content.data))
            return "\n".join(parts) if parts else "（工具返回空结果）"
        except Exception as e:
            return f"工具调用失败：{e}"

    def get_tool_schemas(self) -> list[dict]:
        """把 MCP 工具格式转换成 OpenAI function calling 格式"""
        schemas = []
        for tool in self._tools:
            schemas.append({
                "name":        f"mcp__{self.name}__{tool.name}",   # 命名空间防冲突
                "description": f"[{self.label}] {tool.description or tool.name}",
                "parameters":  tool.inputSchema or {"type": "object", "properties": {}},
                "_mcp_server": self.name,       # 内部字段，dispatch 时用于路由
                "_mcp_tool":   tool.name,
            })
        return schemas

    async def disconnect(self):
        try:
            if self._session:
                await self._session.__aexit__(None, None, None)
            if hasattr(self, "_transport_cm"):
                await self._transport_cm.__aexit__(None, None, None)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# MCP Client Manager（全局单例）
# ══════════════════════════════════════════════════════════════════════════════

class MCPClientManager:
    """
    管理所有 MCP Server 连接的全局管理器。
    应用启动时初始化，之后复用连接。
    """

    def __init__(self):
        self._sessions: dict[str, MCPClientSession] = {}
        self._ready = False

    async def initialize(self, server_configs: list[dict] = None):
        """启动时连接所有启用的 MCP Server"""
        configs = server_configs or EXAMPLE_SERVERS
        enabled = [c for c in configs if c.get("enabled", False)]

        if not enabled:
            print("[MCP Client] 没有启用的 MCP Server，跳过初始化", file=sys.stderr)
            self._ready = True
            return

        tasks = [self._connect_one(c) for c in enabled]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._ready = True
        print(f"[MCP Client] 初始化完成，已连接 {len(self._sessions)} 个 Server", file=sys.stderr)

    async def _connect_one(self, config: dict):
        session = MCPClientSession(config)
        ok = await session.connect()
        if ok:
            self._sessions[config["name"]] = session

    def get_all_tool_schemas(self) -> list[dict]:
        """获取所有已连接 MCP Server 的工具 schema，注入 Agent"""
        schemas = []
        for session in self._sessions.values():
            schemas.extend(session.get_tool_schemas())
        return schemas

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """
        根据工具名路由到对应 MCP Server 执行。
        工具名格式：mcp__{server_name}__{tool_name}
        """
        # 解析命名空间
        parts = tool_name.split("__")
        if len(parts) != 3 or parts[0] != "mcp":
            return f"无效的 MCP 工具名：{tool_name}"

        server_name = parts[1]
        actual_tool = parts[2]

        session = self._sessions.get(server_name)
        if not session:
            return f"MCP Server {server_name} 未连接或未启用"

        return await session.call_tool(actual_tool, arguments)

    async def shutdown(self):
        """应用关闭时断开所有连接"""
        for session in self._sessions.values():
            await session.disconnect()
        self._sessions.clear()

    def is_mcp_tool(self, tool_name: str) -> bool:
        """判断工具名是否属于 MCP 工具"""
        return tool_name.startswith("mcp__")


# 全局单例
mcp_manager = MCPClientManager()