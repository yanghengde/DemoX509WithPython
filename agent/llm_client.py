"""OpenAI 协议模型与 MCP 工具桥接；未配置模型时仍能启动和查询能力。"""
import asyncio
import json
import logging
from openai import AsyncOpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT
from core.tool_catalog import TOOL_POLICIES
from tools import mcp, get_tool_func

logger = logging.getLogger(__name__)
oai_client = None
openai_tools: list[dict] = []


async def register_tools():
    global oai_client
    openai_tools.clear()
    registered = await mcp.list_tools()
    for tool in registered:
        if tool.name not in TOOL_POLICIES or get_tool_func(tool.name) is None:
            raise RuntimeError(f'Tool lacks explicit policy or callable: {tool.name}')
        openai_tools.append({'type': 'function', 'function': {
            'name': tool.name, 'description': tool.description, 'parameters': tool.parameters}})
    if LLM_BASE_URL and LLM_MODEL:
        # Local OpenAI-compatible servers often do not require an actual key.
        oai_client = AsyncOpenAI(api_key=LLM_API_KEY or 'local-not-configured',
                                base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT, max_retries=0)
    return openai_tools


async def close_llm_client():
    global oai_client
    if oai_client is not None:
        await oai_client.close()
        oai_client = None


async def generate_title(message: str) -> str:
    return ' '.join(message.split())[:30] or '新对话'


async def chat_stream(username: str, message: str, session_id: str = None):
    if oai_client is None:
        from agent.memory import get_user_messages, save_user_session, get_active_session, update_session_title
        history = get_user_messages(username, session_id)
        actual = session_id or get_active_session(username)
        first = len(history) == 1
        # Use the retained capability tool even in the no-model fallback.
        capability = json.loads(await get_tool_func('get_capabilities')())
        reply = capability['message'] + '配置 LLM_BASE_URL 和 LLM_MODEL 后可启用模型对话。'
        history.extend([{'role': 'user', 'content': message}, {'role': 'assistant', 'content': reply}])
        if first:
            title = ' '.join(message.split())[:30] or '新对话'
            update_session_title(username, actual, title)
            yield f'data: {json.dumps({"type": "title_update", "title": title, "session_id": actual}, ensure_ascii=False)}\n\n'
        save_user_session(username, actual)
        yield f'data: {json.dumps({"type": "done", "reply": reply}, ensure_ascii=False)}\n\n'
        return
    try:
        from agent.langgraph_runtime import langgraph_chat_stream
        async for event in langgraph_chat_stream(username, message, session_id):
            yield event
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception('Chat stream failed')
        yield f'data: {json.dumps({"type": "error", "message": f"聊天工作流执行失败：{type(exc).__name__}: {exc}"}, ensure_ascii=False)}\n\n'
