"""桌面 MCP 客户端 stdio 入口，不启动 Web 或连接产品。"""
from tools import mcp

if __name__ == '__main__':
    mcp.run(transport='stdio')
