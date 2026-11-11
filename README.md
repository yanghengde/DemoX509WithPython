# Demo 工作台 · Opcenter 物料连接测试

已添加基于 X.509 的 Opcenter 只读接入。启动后打开 <http://127.0.0.1:8031/opcenter>，或从首页侧栏进入“Opcenter 物料”。不需要配置模型或 API Key。

页面支持证书连接检测、物料编码/名称/描述搜索、分页、仅当前版本筛选、完整物料详情、所属分组、UADM 物料配置、原始 JSON 查看及本页 JSON 导出。数据来自当前服务，不提供演示数据。失败后清除上次结果，避免把旧数据作为本次查询结果。

物料使用 **UADM 的 `AppU4DM/odata/DM_Material`** 接口，以 `$expand=Material` 关联编码、名称、版本等基础信息。详情还读取物料类别、供应商、功能代码及序列号管理、追溯、批次、首件检验、重量、体积等 UADM 配置。页面中的基础物料 Id 与 UADM 物料 Id 分别保留，查询总数以 `DM_Material` 为准。

## Opcenter 配置

参考 `C:\source\OpcenterX509-master\OpcenterX509-master\X509CerficationSolution\Program.cs`：使用 RSA 证书签发 RS256 JWT，发送 `Authorization: Bearer <JWT>`。声明采用 `unique_name=certificate.Issuer`、`certthumbprint=certificate.Thumbprint`、`urn:realm=x.509`、`iss=urn:unifiedoauth`、`aud=urn:unified`；令牌有效期默认 120 秒，到期前 15 秒重新签发。

在项目 `.env` 中配置 Windows 证书库方式：

```ini
OPCENTER_BASE_URL=http://opcenter2607/sit-svc/Siemens/Application/AppU4DM/odata
OPCENTER_X509_SOURCE=store
OPCENTER_X509_STORE_LOCATION=LocalMachine
OPCENTER_X509_THUMBPRINT=已在Opcenter中授权的证书指纹
```

本机 `.env` 已选择现有的 Opcenter 系统证书用于本次连接测试，私钥保留在 `LocalMachine/My`，没有创建或导出私钥，也没有修改 Opcenter 的证书信任或角色。换机器时需要改为在目标环境已授权、带 RSA 私钥的证书；运行账号须具备私钥读取权限。正式部署建议配置独立的集成证书。

与参考 C# 相同，也支持 PFX：

```ini
OPCENTER_X509_SOURCE=pfx
OPCENTER_X509_THUMBPRINT=
OPCENTER_X509_PFX_PATH=C:/certs/integration.pfx
OPCENTER_X509_PFX_PASSWORD=你的证书密码
```

PFX 相对路径以项目目录为基准。证书必须有效并包含至少 2048 位 RSA 私钥。修改 `.env` 后重启 Demo。私钥、密码和 JWT 不返回浏览器；`.env`、PFX 和运行数据已加入 Git 忽略规则。HTTPS 默认校验证书，本机请求不使用系统代理，不跟随重定向或服务提供的外部分页地址。

## 独立测试程序与 API

```powershell
cd C:\source\Demo
.\.venv\Scripts\python.exe connect_opcenter.py --top 5
.\.venv\Scripts\python.exe connect_opcenter.py --top 10 --keyword "物料编码"
```

程序先校验 `AppU4DM/odata/$metadata` 中的 `DM_Material → Material` 关联契约，再读取 `DM_Material`；成功输出连接摘要和真实物料 JSON，失败输出可读诊断并退出 1。独立测试与网页共用同一个认证和物料适配器。

| 本地 API | 用途 |
| --- | --- |
| `GET /api/opcenter/config` | 读取不含密码和令牌的连接配置 |
| `POST /api/opcenter/connection` | 签名并验证元数据、物料读取 |
| `GET /api/opcenter/materials?keyword=&top=20&skip=0&current_only=false` | 查询物料列表，单页 1–100 条 |
| `GET /api/opcenter/materials/{基础物料GUID}` | 读取关联 Material、分组及 UADM 配置、物料类别、供应商、功能代码 |

搜索作为 OData 字符串安全转义，使用 `contains(Material/NId,...)` 等表达式；版本筛选使用 `Material/IsCurrent eq true`，排序使用 `Material/NId asc,Id asc`。实体、排序和字段由后端固定，拒绝任意 API 地址或表达式。接口通过元数据确认真实 `DM_Material`、`Material_Id` 和 `Material` 关联；不包含 UADM 契约的服务会被拒绝。HTTP 200 的登录 HTML 不会被误报为成功。`$count` 返回的总数和当前筛选一致。

本地文档入口：[Opcenter Documentation Center](http://opcenter2607/sit-ui/system/doc/index.html)。物料契约使用当前安装版本的 `AppU4DM/odata/$metadata`；基础字段包括 Id、NId、Name、Description、Revision、UoMNId、IsCurrent、LastUpdatedOn。列表返回基础 `Id` 和 `DM_MaterialId`；详情的 `UADM` 对象保留完整 `DM_Material` 内容。相关实现位于 `connectors/opcenter/`、`web/opcenter.py` 和 `static/opcenter.*`。

若返回 **503 / Application is under maintenance**，表示业务服务暂时维护不可用；恢复后点击“测试连接”重新读取。401 需检查信任、角色映射、issuer/audience 和时钟；403 需检查角色读取权限；404 需检查路径或物料 Id。程序不会自动修改 Opcenter 的部署、证书授权或维护状态。

## 原有框架

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
