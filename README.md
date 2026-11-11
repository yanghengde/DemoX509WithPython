# Demo 空框架

沿用 `C:\source\OpcenterEXMCP` 的技术栈和工作台结构，移除了产品业务、专用认证、业务提示词及业务工具。未复制原项目 `.env`、证书、运行数据或聊天记录。

| 层 | 技术 |
| --- | --- |
| 运行环境 | Python 3.12，独立 `.venv` |
| Web | FastAPI + Uvicorn |
| MCP | FastMCP，stdio / 可选 Streamable HTTP |
| Agent | LangGraph + OpenAI 兼容协议，SSE 流式事件 |
| 界面 | 原生 HTML / CSS / JavaScript，本地 Markdown 渲染与净化 |
| 存储 | SQLite 检查点和后台任务，本地 JSON 会话 |

## 启动

当前目录已安装依赖，直接执行：

```powershell
cd C:\source\Demo
.\start.ps1
```

打开 <http://127.0.0.1:8031/>，日志页 <http://127.0.0.1:8031/logs>，健康检查 <http://127.0.0.1:8031/health>。默认端口与参考项目的 8030 分开。

无需 `.env`、模型 Key 或产品连接即可空跑。未配置模型时，聊天返回框架状态说明。首页没有业务示例或预置数据。保留新建会话、历史记录、后台任务恢复与停止、确认流程、附件暂存和运行日志。

可复制 `.env.example` 为 `.env` 修改端口；配置 `LLM_BASE_URL`、`LLM_MODEL` 及服务商所需的 `LLM_API_KEY` 后启用模型。所有运行数据只保存在本项目 `data/`，默认绑定本机。

## 重新安装

需要 Python 3.12；如果系统没有 `py` 命令，可使用实际 Python 3.12 路径替换首行的 `py -3.12`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

`requirements.txt` 是运行依赖，`requirements-dev.txt` 增加 pytest，`requirements-lock.txt` 记录本次验证的实际版本。

## MCP

stdio 客户端配置：

```json
{
  "mcpServers": {
    "demo": {
      "command": "C:\\source\\Demo\\.venv\\Scripts\\python.exe",
      "args": ["C:\\source\\Demo\\mcp_server.py"]
    }
  }
}
```

默认只有只读框架工具 `get_capabilities`，业务工具数量为 0。若要启用 HTTP MCP，在 `.env` 配置 `ENABLE_MCP_HTTP=True` 和非空 `MCP_API_KEY`，重启后通过 `/mcp/` 接入，请求携带 `Authorization: Bearer <MCP_API_KEY>`。

## 扩展位置

- `web/`：Web 路由、后台聊天任务、MCP HTTP。
- `static/`：工作台界面和本地前端依赖。
- `agent/`：模型桥接、工作流、会话、确认策略。
- `tools/`：MCP 工具注册和调用白名单。
- `core/tool_catalog.py`：显式工具策略；新增工具需要同步维护。
- `core/http_client.py`：通用 HTTP 连接池，默认未配置且不会自动请求外部服务。
- `connectors/`：预留业务适配器目录。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试隔离到临时目录，使用模拟模型，不需要真实产品或模型密钥。工作台为单进程本地应用，`APP_USERNAME` 是会话标识；附件仅暂存，无解析或导入逻辑。
