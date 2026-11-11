"""FastAPI 工厂与 MCP HTTP 生命周期。"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from config import (STATIC_DIR, ENABLE_MCP_HTTP, MCP_API_KEY, MCP_HTTP_PATH,
                    MCP_ALLOWED_HOSTS, MCP_ALLOWED_ORIGINS)
from agent.memory import init_memory
from agent import llm_client
from tools import mcp
from web.routes import router
from web.opcenter import router as opcenter_router
from web.chat_runs import RUNS
from web.mcp_transport import BearerTokenMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_memory()
    RUNS.initialize()
    await llm_client.register_tools()
    from agent.langgraph_runtime import init_langgraph_runtime, close_langgraph_runtime
    await init_langgraph_runtime(llm_client.oai_client, llm_client.openai_tools)
    try:
        transport = app.state.mcp_http_app
        if transport is not None:
            async with transport.router.lifespan_context(transport):
                yield
        else:
            yield
    finally:
        if app.state.opcenter is not None:
            await app.state.opcenter.close()
        await RUNS.shutdown()
        await close_langgraph_runtime()
        await llm_client.close_llm_client()
        from core.http_client import close_client
        await close_client()


def create_app() -> FastAPI:
    app = FastAPI(title='Demo 工作台', lifespan=lifespan)
    app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')
    app.include_router(router)
    app.include_router(opcenter_router)
    app.state.opcenter = None
    app.state.mcp_http_app = None
    if ENABLE_MCP_HTTP:
        if not MCP_API_KEY:
            raise RuntimeError('ENABLE_MCP_HTTP=True requires MCP_API_KEY')
        transport = mcp.http_app(path='/', transport='streamable-http', stateless_http=True,
                                 json_response=True, allowed_hosts=MCP_ALLOWED_HOSTS,
                                 allowed_origins=MCP_ALLOWED_ORIGINS or None)
        app.state.mcp_http_app = transport
        app.mount(MCP_HTTP_PATH, BearerTokenMiddleware(transport, MCP_API_KEY), name='mcp')
    return app
