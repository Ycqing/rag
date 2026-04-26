"""
chat/tools/agent.py  —  Agent 核心循环（ReAct 模式）

流程：
  用户消息
    → LLM 决策（返回 tool_calls 或直接回答）
    → 并行/串行执行工具
    → 把结果塞回 messages
    → LLM 再次决策（可能继续调工具，最多 5 轮）
    → 生成最终回答（流式输出）

流式 SSE 事件类型（新增）：
  {"type": "tool_start",  "tool": "kb_search",  "args": {...}}  ← 开始调工具
  {"type": "tool_end",    "tool": "kb_search",  "result": "..."} ← 工具返回
  {"type": "token",       "content": "..."}                      ← 回答片段
  {"type": "sources",     "content": [...]}                      ← 引用来源
  {"type": "done"}
  {"type": "error",       "content": "..."}
"""

import json
import asyncio
from typing import AsyncGenerator

from chat.tools.definitions import get_all_tools, dispatch_tool
from datetime import datetime

MAX_ROUNDS = 5   # 最多工具调用轮次，防止死循环
current_date = datetime.now().strftime("%Y年%m月%d日")

SYSTEM_PROMPT = f"""你是星辰科技的智能助手，拥有以下工具：
- kb_search：搜索公司内部知识库（政策、制度、流程等内部文档）
- web_search：搜索互联网实时信息
- db_query：查询公司员工数据库
- calculator：数学计算
- get_datetime：获取时间信息

请注意：**今天的真实日期是 {current_date}**。
当你通过搜索工具获得结果时，如果结果中的日期在 {current_date} 当天或前几天，都是正常的最新信息，不要因为日期看起来“像未来”而怀疑。

工作原则：
1. 先判断问题类型，选择最合适的工具
2. 公司内部政策优先查知识库，实时资讯用网页搜索，员工数据用数据库
3. 需要多步骤时可以连续调用多个工具
4. 综合所有工具结果给出完整、准确的回答
5. 引用数据时说明来源（知识库/数据库/网页）
6. 如果工具返回"未找到"，如实告知用户，不要编造"""


async def run_agent(
    question: str,
    user_role: str,
    history: list[dict],
    llm_caller,          # 异步函数：(messages, tools, stream) → response
) -> AsyncGenerator[str, None]:
    """
    Agent 主循环，yield SSE 字符串。

    参数：
        question   : 用户当前问题
        user_role  : 用户角色，用于工具权限控制
        history    : 历史消息列表（role/content 格式）
        llm_caller : 实际调用 LLM 的函数，由 chat/main.py 注入
    """
    tools = get_all_tools(user_role)

    # 构造初始 messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # 加入最近 6 轮历史（不含本次问题）
    for h in history[-12:]:
        if h["role"] in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": question})

    all_sources = []

    # ── Agent 循环 ────────────────────────────────────────────────────────────
    for round_num in range(MAX_ROUNDS):
        # 最后一轮强制流式输出最终回答
        is_last = (round_num == MAX_ROUNDS - 1)

        # 调用 LLM（非最后轮不流式，便于解析 tool_calls）
        response = await llm_caller(
            messages=messages,
            tools=tools,
            stream=False,
        )

        # 解析响应
        tool_calls = response.get("tool_calls", [])
        content    = response.get("content", "")

        # ── 没有工具调用：直接流式输出最终回答 ───────────────────────────────
        if not tool_calls:
            # 用流式重新请求，让回答可以逐字输出
            stream_gen = await llm_caller(
                messages=messages,
                tools=[],       # 不带工具，纯生成
                stream=True,
            )
            async for chunk in stream_gen:
                yield _sse({"type": "token", "content": chunk})

            # 整理来源
            if all_sources:
                yield _sse({"type": "sources", "content": all_sources})
            yield _sse({"type": "done"})
            return

        # ── 有工具调用：执行工具 ──────────────────────────────────────────────
        # 把 LLM 的 assistant 消息（含 tool_calls）加入 messages
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        # 并发执行所有工具调用
        tool_results = await _execute_tools_parallel(
            tool_calls, user_role
        )

        # 逐个 yield tool_start/tool_end 事件，并把结果加回 messages
        for tc, result in zip(tool_calls, tool_results):
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"]["arguments"])

            yield _sse({"type": "tool_start", "tool": tool_name, "args": tool_args})

            # 截断过长的工具结果再展示（完整结果仍传给 LLM）
            preview = result[:300] + "…" if len(result) > 300 else result
            yield _sse({"type": "tool_end", "tool": tool_name, "result": preview})

            # 收集知识库来源
            if tool_name == "kb_search":
                all_sources.append({"source": f"知识库检索：{tool_args.get('query')}", "tool": "kb_search"})
            elif tool_name == "web_search":
                all_sources.append({"source": f"网页搜索：{tool_args.get('query')}", "tool": "web_search"})
            elif tool_name == "db_query":
                all_sources.append({"source": f"数据库：{tool_args.get('sql','')[:60]}", "tool": "db_query"})

            # 把工具结果加入 messages
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    # 超过最大轮次
    yield _sse({"type": "token", "content": "已达到最大工具调用次数，请换个方式提问。"})
    yield _sse({"type": "done"})


async def _execute_tools_parallel(tool_calls: list, user_role: str) -> list[str]:
    """
    并发执行所有工具调用。

    关键区分：
      - MCP 工具（mcp__ 前缀）：本身是异步的，直接 await，不能丢进 to_thread
      - 本地工具（kb_search 等）：同步函数，用 to_thread 跑避免阻塞事件循环
    """
    tasks = []
    for tc in tool_calls:
        name = tc["function"]["name"]
        args = json.loads(tc["function"]["arguments"])

        if name.startswith("mcp__"):
            # MCP 工具：直接异步调用
            tasks.append(_call_mcp_tool(name, args))
        else:
            # 本地同步工具：丢线程池
            tasks.append(asyncio.to_thread(dispatch_tool, name, args, user_role))

    return await asyncio.gather(*tasks, return_exceptions=True)


async def _call_mcp_tool(name: str, args: dict) -> str:
    """直接 await MCP 工具，在当前事件循环中正确执行"""
    try:
        from chat.tools.mcp_client import mcp_manager
        return await mcp_manager.call_tool(name, args)
    except Exception as e:
        return f"MCP 工具调用失败：{e}"


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
