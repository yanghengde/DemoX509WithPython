"""工具策略与 MCP 注册需同时维护，禁止按函数名称猜测写入风险。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolPolicy:
    group: str
    effect: str = 'read'
    default_visible: bool = True
    always_confirm: bool = False
    manual_confirm_only: bool = False


TOOL_POLICIES = {'get_capabilities': ToolPolicy('system')}
