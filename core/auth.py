"""通用 HTTP 认证。产品专用登录/刷新协议需由产品适配器实现。"""
import httpx


def authentication(mode: str, *, username='', password='', token='', api_key='',
                   api_key_header='X-API-Key') -> tuple[dict[str, str], httpx.Auth | None]:
    if mode == 'none':
        return {}, None
    if mode == 'basic':
        if not username or not password:
            raise ValueError('Basic 认证需要 PRODUCT_USERNAME 和 PRODUCT_PASSWORD')
        return {}, httpx.BasicAuth(username, password)
    if mode == 'bearer':
        if not token:
            raise ValueError('Bearer 认证需要 PRODUCT_TOKEN')
        return {'Authorization': f'Bearer {token}'}, None
    if mode == 'api_key':
        if not api_key or not api_key_header:
            raise ValueError('API Key 认证需要密钥和请求头名称')
        return {api_key_header: api_key}, None
    raise ValueError('未知 PRODUCT_AUTH_MODE；支持 none/basic/bearer/api_key')
