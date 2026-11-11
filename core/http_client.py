"""产品无关的异步连接池。未注册为 MCP 工具，不自动探测或重试写入。"""
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit
import httpx
import config
from core.auth import authentication


@dataclass(frozen=True)
class ProductConnection:
    base_url: str = ''
    auth_mode: str = 'none'
    username: str = ''
    password: str = ''
    token: str = ''
    api_key: str = ''
    api_key_header: str = 'X-API-Key'
    timeout: float = 30
    verify_tls: bool = True
    trust_env: bool = False

    @classmethod
    def from_config(cls):
        return cls(config.PRODUCT_BASE_URL, config.PRODUCT_AUTH_MODE,
                   config.PRODUCT_USERNAME, config.PRODUCT_PASSWORD, config.PRODUCT_TOKEN,
                   config.PRODUCT_API_KEY, config.PRODUCT_API_KEY_HEADER,
                   config.PRODUCT_TIMEOUT, config.PRODUCT_VERIFY_TLS, config.PRODUCT_TRUST_ENV)


class ProductClient:
    def __init__(self, connection: ProductConnection, *, transport=None):
        self.connection = connection
        self._transport = transport
        self._client = None

    def build_url(self, path: str) -> str:
        base = self.connection.base_url.rstrip('/')
        parsed = urlsplit(base)
        if not base:
            raise ValueError('产品尚未配置：请设置 PRODUCT_BASE_URL')
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('PRODUCT_BASE_URL 必须是无凭据、查询参数和片段的 HTTP(S) 地址')
        target = urlsplit(path)
        decoded = unquote(path).replace('\\', '/')
        if target.scheme or target.netloc or target.query or target.fragment or '?' in path or '#' in path or '\\' in path or any(part in {'.', '..'} for part in decoded.split('/')):
            raise ValueError('只允许当前产品下的相对 API 路径；查询请使用 params')
        return base + '/' + path.lstrip('/')

    async def request(self, method: str, path: str, *, body=None, params=None, headers=None) -> httpx.Response:
        url = self.build_url(path)
        if self._client is None or self._client.is_closed:
            common_headers, auth = authentication(
                self.connection.auth_mode, username=self.connection.username,
                password=self.connection.password, token=self.connection.token,
                api_key=self.connection.api_key, api_key_header=self.connection.api_key_header)
            self._client = httpx.AsyncClient(
                headers={'Accept': 'application/json', **common_headers}, auth=auth,
                timeout=self.connection.timeout, verify=self.connection.verify_tls,
                trust_env=self.connection.trust_env, follow_redirects=False, transport=self._transport)
        response = await self._client.request(method, url, json=body, params=params, headers=headers)
        response.raise_for_status()
        return response

    async def close(self):
        if self._client is not None:
            await self._client.aclose()


_client: ProductClient | None = None


def get_client() -> ProductClient:
    global _client
    if _client is None:
        _client = ProductClient(ProductConnection.from_config())
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.close()
        _client = None
