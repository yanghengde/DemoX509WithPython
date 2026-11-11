"""在通用连接池上注入短期 X.509 Bearer，不暴露任意请求 MCP 工具。"""
import asyncio
from dataclasses import replace
from core.http_client import ProductClient, ProductConnection
from connectors.opcenter.x509 import CertificateTokenProvider, X509Settings


class OpcenterX509Client(ProductClient):
    def __init__(self, connection: ProductConnection, settings: X509Settings, *, transport=None):
        super().__init__(replace(connection, auth_mode='none'), transport=transport)
        self.tokens = CertificateTokenProvider(settings)

    async def request(self, method: str, path: str, *, body=None, params=None, headers=None):
        self.build_url(path)  # Validate the destination before using the private key.
        token = await asyncio.to_thread(self.tokens.token)
        return await super().request(method, path, body=body, params=params,
                                     headers={**(headers or {}), 'Authorization': f'Bearer {token}'})

    @classmethod
    def from_config(cls):
        import config
        return cls(ProductConnection(base_url=config.OPCENTER_BASE_URL,
                                     timeout=config.OPCENTER_TIMEOUT,
                                     verify_tls=config.OPCENTER_VERIFY_TLS), X509Settings.from_config())
