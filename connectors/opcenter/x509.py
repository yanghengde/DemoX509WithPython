"""与参考 C# 示例相同的 RS256、证书身份声明和两分钟 JWT。"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import subprocess
import threading
import time

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from cryptography.x509.oid import NameOID


class CertificateError(ValueError):
    """无密钥或令牌内容的证书诊断。"""


@dataclass(frozen=True)
class X509Settings:
    source: str = 'store'
    thumbprint: str = ''
    store_location: str = 'LocalMachine'
    pfx_path: str = ''
    pfx_password: str = field(default='', repr=False)
    issuer: str = 'urn:unifiedoauth'
    audience: str = 'urn:unified'
    lifetime_seconds: int = 120

    def __post_init__(self):
        if self.source not in {'store', 'pfx'}:
            raise CertificateError('OPCENTER_X509_SOURCE 只支持 store/pfx')
        if not 30 <= self.lifetime_seconds <= 120:
            raise CertificateError('JWT 有效期必须为 30–120 秒')

    @classmethod
    def from_config(cls):
        import config
        return cls(source=config.OPCENTER_X509_SOURCE,
                   thumbprint=config.OPCENTER_X509_THUMBPRINT,
                   store_location=config.OPCENTER_X509_STORE_LOCATION,
                   pfx_path=config.OPCENTER_X509_PFX_PATH,
                   pfx_password=config.OPCENTER_X509_PFX_PASSWORD,
                   issuer=config.OPCENTER_X509_ISSUER,
                   audience=config.OPCENTER_X509_AUDIENCE,
                   lifetime_seconds=config.OPCENTER_X509_LIFETIME)


def _from_pfx(settings: X509Settings) -> tuple[str, int]:
    if not settings.pfx_path:
        raise CertificateError('请设置 OPCENTER_X509_PFX_PATH')
    try:
        content = Path(settings.pfx_path).read_bytes()
    except OSError:
        raise CertificateError('无法读取 PFX 文件，请检查路径和权限') from None
    try:
        key, cert, _ = load_key_and_certificates(
            content, settings.pfx_password.encode('utf-8') if settings.pfx_password else None)
    except ValueError:
        raise CertificateError('无法解密 PFX，请检查文件格式和密码') from None
    if cert is None or not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise CertificateError('PFX 必须包含证书和至少 2048 位的 RSA 私钥')
    now = int(time.time())
    expires = now + settings.lifetime_seconds
    if cert.not_valid_before_utc > datetime.fromtimestamp(now, timezone.utc) or cert.not_valid_after_utc <= datetime.fromtimestamp(expires, timezone.utc):
        raise CertificateError('证书尚未生效、已过期或不足以覆盖本次令牌有效期')
    thumbprint = cert.fingerprint(hashes.SHA1()).hex().upper()
    if settings.thumbprint and settings.thumbprint.replace(' ', '').upper() != thumbprint:
        raise CertificateError('PFX 与配置的证书指纹不匹配')
    claims = {
        'unique_name': ', '.join(rdn.rfc4514_string({NameOID.TITLE: 'T', NameOID.STATE_OR_PROVINCE_NAME: 'S'})
                                 for rdn in reversed(cert.issuer.rdns)),
        'certthumbprint': thumbprint, 'urn:realm': 'x.509',
        'iss': settings.issuer, 'aud': settings.audience,
        'iat': now, 'nbf': now, 'exp': expires,
    }
    import base64
    x5t = base64.urlsafe_b64encode(cert.fingerprint(hashes.SHA1())).rstrip(b'=').decode('ascii')
    token = jwt.encode(claims, key, algorithm='RS256', headers={'kid': thumbprint, 'x5t': x5t})
    return token, expires


def _from_store(settings: X509Settings) -> tuple[str, int]:
    if os.name != 'nt':
        raise CertificateError('Windows 证书库方式仅支持 Windows；其他系统请使用 PFX')
    import re
    thumbprint = settings.thumbprint.replace(' ', '').upper()
    if not re.fullmatch('[0-9A-F]{40}', thumbprint):
        raise CertificateError('请设置完整的 OPCENTER_X509_THUMBPRINT')
    if settings.store_location not in {'LocalMachine', 'CurrentUser'}:
        raise CertificateError('证书库位置仅支持 LocalMachine/CurrentUser')
    script = Path(__file__).with_name('sign_store_token.ps1')
    executable = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    try:
        result = subprocess.run(
            [executable, '-NoProfile', '-NonInteractive', '-File', str(script)],
            input=json.dumps({'thumbprint': thumbprint, 'store_location': settings.store_location,
                              'issuer': settings.issuer, 'audience': settings.audience,
                              'lifetime_seconds': settings.lifetime_seconds}),
            capture_output=True, encoding='utf-8', timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        raise CertificateError('无法运行 Windows 证书签名进程或签名超时') from None
    if result.returncode:
        # The controlled helper only emits non-secret certificate diagnostics.
        raise CertificateError(result.stderr.strip() or 'Windows 证书签名失败')
    try:
        data = json.loads(result.stdout)
        return data['token'], int(data['expires_at'])
    except (ValueError, KeyError, TypeError):
        raise CertificateError('Windows 证书签名进程返回了无效数据') from None


class CertificateTokenProvider:
    """令牌仅在内存中缓存，到期前 15 秒重新签发。"""
    def __init__(self, settings: X509Settings):
        self.settings = settings
        self._token = ''
        self._expires = 0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if time.time() >= self._expires - 15:
                self._token, self._expires = (_from_store(self.settings) if self.settings.source == 'store'
                                               else _from_pfx(self.settings))
            return self._token
