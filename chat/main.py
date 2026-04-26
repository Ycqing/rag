"""
chat/main.py（v2）—  接入 Agent 工具调用

启动：
  cd rag_rbac
  uvicorn chat.main:app --reload --port 8002
"""

import sys, json, datetime, asyncio
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import jwt

import config
from admin.services.store import get_user_by_username, init_db
from chat.tools.agent import run_agent

app      = FastAPI(title="星辰科技知识库问答 v2")
security = HTTPBearer()

JWT_SECRET    = "startech-secret-change-in-production"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_H  = 8
HISTORY: dict[str, list] = {}
MAX_HISTORY = 40


# ── JWT ──────────────────────────────────────────────────────────────────────
def make_token(user):
    payload = {
        "sub": user["id"], "username": user["username"],
        "role": user["role"], "name": user["name"],
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRE_H),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "登录已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "无效凭证")

def current_user(cred: HTTPAuthorizationCredentials = Depends(security)):
    return decode_token(cred.credentials)


# ── 路由 ─────────────────────────────────────────────────────────────────────
class LoginReq(BaseModel):
    username: str
    password: str

@app.post("/auth/login")
def login(req: LoginReq):
    user = get_user_by_username(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(401, "用户名或密码错误")
    return {"token": make_token(user), "user": {
        "id": user["id"], "username": user["username"],
        "name": user["name"], "role": user["role"],
    }}

@app.get("/api/me")
def me(user=Depends(current_user)):
    return {**user, "accessible_roles": config.get_accessible_roles(user["role"])}

@app.get("/api/history")
def get_history(user=Depends(current_user)):
    return HISTORY.get(user["sub"], [])

@app.delete("/api/history")
def clear_history(user=Depends(current_user)):
    HISTORY[user["sub"]] = []
    return {"message": "已清空"}

@app.get("/api/tools")
def list_tools(user=Depends(current_user)):
    from chat.tools.definitions import get_all_tools
    tools = get_all_tools(user["role"])
    return [{"name": t["name"], "description": t["description"][:80] + "…"} for t in tools]

class ChatReq(BaseModel):
    message: str

@app.post("/api/chat")
async def chat(req: ChatReq, user=Depends(current_user)):
    user_id = user["sub"]
    role    = user["role"]
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    if user_id not in HISTORY:
        HISTORY[user_id] = []
    HISTORY[user_id].append({"role": "user", "content": message, "ts": _now()})
    return StreamingResponse(
        _stream(user_id, role, message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Agent 流 ──────────────────────────────────────────────────────────────────
async def _stream(user_id, role, question):
    full_answer = ""
    sources = []
    try:
        async for sse in run_agent(
            question=question,
            user_role=role,
            history=HISTORY.get(user_id, []),
            llm_caller=_llm_caller,
        ):
            yield sse
            try:
                evt = json.loads(sse.removeprefix("data: ").strip())
                if evt["type"] == "token":   full_answer += evt["content"]
                elif evt["type"] == "sources": sources = evt["content"]
            except Exception:
                pass
    except Exception as e:
        yield f"data: {json.dumps({'type':'error','content':str(e)})}\n\n"

    HISTORY[user_id].append({
        "role": "assistant", "content": full_answer,
        "sources": sources, "ts": _now()
    })
    if len(HISTORY[user_id]) > MAX_HISTORY:
        HISTORY[user_id] = HISTORY[user_id][-MAX_HISTORY:]


# ── LLM Caller ────────────────────────────────────────────────────────────────
async def _llm_caller(messages, tools, stream):
    if config.DASHSCOPE_API_KEY in ("sk-xxx", "", None):
        return await _mock_llm(messages, tools, stream)

    import dashscope
    from dashscope import Generation
    dashscope.api_key = config.DASHSCOPE_API_KEY

    kwargs = dict(model=config.LLM_MODEL_NAME, messages=messages,
                  stream=stream, incremental_output=stream)
    if tools:
        kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
        kwargs["tool_choice"] = "auto"

    if stream:
        async def _gen():
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()
            def _call():
                try:
                    for resp in Generation.call(**kwargs):
                        choices = (resp.output or {}).get("choices") or []
                        txt = choices[0].get("message", {}).get("content", "") if choices else \
                              (resp.output or {}).get("text", "")
                        if txt:
                            loop.call_soon_threadsafe(q.put_nowait, txt)
                except Exception as e:
                    loop.call_soon_threadsafe(q.put_nowait, Exception(str(e)))
                finally:
                    loop.call_soon_threadsafe(q.put_nowait, None)
            loop.run_in_executor(None, _call)
            while True:
                item = await q.get()
                if item is None: break
                if isinstance(item, Exception): raise item
                yield item
        return _gen()
    else:
        resp = await asyncio.to_thread(Generation.call, **kwargs)
        msg  = ((resp.output or {}).get("choices") or [{}])[0].get("message", {})
        return {"content": msg.get("content", ""), "tool_calls": msg.get("tool_calls", [])}


async def _mock_llm(messages, tools, stream):
    """无 API Key 时模拟工具路由，演示 Agent 完整流程"""
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    if tools:
        if any(k in last for k in ["年假", "报销", "制度", "规定", "考勤", "手册"]):
            return {"content": "", "tool_calls": [{"id": "m1", "function": {
                "name": "kb_search",
                "arguments": json.dumps({"query": last}, ensure_ascii=False)}}]}
        if any(k in last for k in ["员工", "部门", "谁", "人数", "职级", "入职"]):
            return {"content": "", "tool_calls": [{"id": "m2", "function": {
                "name": "db_query",
                "arguments": json.dumps({"sql": "SELECT name,dept,level,status FROM employees LIMIT 10"},
                                        ensure_ascii=False)}}]}
        if any(k in last for k in ["计算", "多少钱", "税后", "合计"]):
            return {"content": "", "tool_calls": [{"id": "m3", "function": {
                "name": "calculator",
                "arguments": json.dumps({"expression": "28000 * 0.8"}, ensure_ascii=False)}}]}
        if any(k in last for k in ["今天", "时间", "日期", "工作日"]):
            return {"content": "", "tool_calls": [{"id": "m4", "function": {
                "name": "get_datetime",
                "arguments": json.dumps({"query_type": "now"}, ensure_ascii=False)}}]}

    mock = "（模拟）请配置 DASHSCOPE_API_KEY 使用真实 LLM。当前展示工具调用完整流程。"
    if stream:
        async def _g():
            for ch in mock:
                yield ch
                await asyncio.sleep(0.02)
        return _g()
    return {"content": mock, "tool_calls": []}


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 静态文件 ──────────────────────────────────────────────────────────────────
STATIC = Path(__file__).parent / "static"

@app.on_event("startup")
async def startup():
    init_db()
    try:
        from chat.tools.mcp_client import mcp_manager
        await mcp_manager.initialize()
    except ImportError:
        pass


@app.on_event("shutdown")
async def shutdown():
    try:
        from chat.tools.mcp_client import mcp_manager
        await mcp_manager.shutdown()
    except Exception:
        pass


app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/")
@app.get("/{full_path:path}")
def frontend(full_path: str = ""):
    return FileResponse(STATIC / "index.html")
