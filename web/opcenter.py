"""本地工作台的 Opcenter 连接测试和物料页面 API。"""
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

import config
from connectors.opcenter.client import OpcenterX509Client
from connectors.opcenter.materials import MaterialNotFound, MaterialService
from connectors.opcenter.x509 import CertificateError

router = APIRouter()


def service(request: Request) -> MaterialService:
    if request.app.state.opcenter is None:
        request.app.state.opcenter = MaterialService(OpcenterX509Client.from_config())
    return request.app.state.opcenter


async def call(operation):
    try:
        return await operation()
    except CertificateError as exc:
        raise HTTPException(400, {'code': 'certificate_error', 'message': str(exc)}) from None
    except MaterialNotFound as exc:
        raise HTTPException(404, {'code': 'material_not_found', 'message': str(exc)}) from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        message = {401: 'Opcenter 拒绝证书身份，请检查证书信任、角色映射、issuer/audience 和系统时间。',
                   403: '证书已提交，但当前角色没有物料读取权限。',
                   404: '未找到 Opcenter 接口或物料，请核对服务路径和物料 Id。',
                   503: 'Opcenter 业务服务暂不可用，请检查服务运行状态。'}.get(status, 'Opcenter 请求失败，请检查服务日志。')
        code = 'upstream_error'
        if status == 503 and 'under maintenance' in exc.response.text.lower():
            code = 'maintenance'
            message = 'Opcenter 正在维护（Application is under maintenance），恢复后请重新测试连接。'
        raise HTTPException(503 if status == 503 else 404 if status == 404 else 502,
                            {'code': code, 'message': message, 'upstream_status': status}) from None
    except httpx.TimeoutException:
        raise HTTPException(504, {'code': 'timeout', 'message': 'Opcenter 请求超时，请检查服务状态和网络。'}) from None
    except httpx.RequestError:
        raise HTTPException(502, {'code': 'network_error', 'message': '无法连接 Opcenter，请检查主机名、网络和 HTTPS 证书。'}) from None
    except ValueError as exc:
        raise HTTPException(502, {'code': 'invalid_response', 'message': str(exc)}) from None


@router.get('/opcenter', response_class=HTMLResponse)
def page():
    return (config.STATIC_DIR / 'opcenter.html').read_text(encoding='utf-8')


@router.get('/api/opcenter/config')
def connection_config():
    return {'base_url': config.OPCENTER_BASE_URL, 'auth_mode': 'X.509 / RS256',
            'module': 'AppU4DM', 'entity': 'DM_Material',
            'certificate_source': config.OPCENTER_X509_SOURCE,
            'store_location': config.OPCENTER_X509_STORE_LOCATION,
            'thumbprint': config.OPCENTER_X509_THUMBPRINT,
            'certificate_configured': bool(config.OPCENTER_X509_THUMBPRINT if config.OPCENTER_X509_SOURCE == 'store'
                                           else config.OPCENTER_X509_PFX_PATH),
            'token_lifetime_seconds': config.OPCENTER_X509_LIFETIME}


@router.post('/api/opcenter/connection')
async def connection(request: Request):
    return await call(lambda: service(request).check_connection())


@router.get('/api/opcenter/materials')
async def materials(request: Request, keyword: str = Query('', max_length=120),
                    top: int = Query(20, ge=1, le=100), skip: int = Query(0, ge=0, le=1_000_000),
                    current_only: bool = False):
    return await call(lambda: service(request).list_materials(keyword=keyword, top=top, skip=skip,
                                                             current_only=current_only))


@router.get('/api/opcenter/materials/{material_id}')
async def material(request: Request, material_id: UUID):
    return await call(lambda: service(request).material(material_id))
