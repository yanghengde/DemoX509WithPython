"""MCP 注册入口；新增工具时同步维护策略和调用白名单。"""
from fastmcp import FastMCP

mcp = FastMCP('DemoFramework', instructions='通用空框架，仅提供框架能力查询，尚未接入业务工具。')

from tools.tool_discovery import get_capabilities

CALLABLES = {'get_capabilities': get_capabilities}


def get_tool_func(name: str):
    return CALLABLES.get(name)
