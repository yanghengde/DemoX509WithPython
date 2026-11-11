"""框架能力查询，不发起外部请求。"""
import json
from config import PRODUCT_NAME
from tools import mcp


@mcp.tool
async def get_capabilities(group: str | None = None, tool_name: str | None = None) -> str:
    """查看框架已注册的工具和参数；不连接业务服务。"""
    registered = {tool.name: tool for tool in await mcp.list_tools()}
    result = {
        'product': PRODUCT_NAME,
        'status': 'framework_only',
        'message': '当前为空框架，尚未接入业务功能。',
        'business_capabilities': [],
        'business_tool_count': 0,
        'registered_tool_count': len(registered),
        'tools': sorted(registered),
        'groups': {'system': sorted(registered)},
        'live_health_check': False,
    }
    if group:
        result['group'] = group
        result['group_tools'] = result['groups'].get(group, [])
    if tool_name:
        tool = registered.get(tool_name)
        result['tool'] = ({'name': tool.name, 'description': tool.description,
                           'parameters': tool.parameters} if tool else None)
    return json.dumps(result, ensure_ascii=False)
