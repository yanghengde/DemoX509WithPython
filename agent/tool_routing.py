"""按显式策略选择通用工具。"""
import json
from core.tool_catalog import TOOL_POLICIES

ROUTING_INSTRUCTION = '查询当前能力时先使用 get_capabilities；仅调用已注册且可见的工具。'


def select_tools(all_tools: list[dict], messages: list[dict]) -> list[dict]:
    return [tool for tool in all_tools
            if (policy := TOOL_POLICIES.get(tool['function']['name'])) and policy.default_visible]


def selection_metrics(selected: list[dict], total: int) -> dict:
    return {'available_tool_count': total, 'selected_tool_count': len(selected),
            'tool_schema_chars': len(json.dumps(selected, ensure_ascii=False)),
            'selected_tools': [tool['function']['name'] for tool in selected]}
