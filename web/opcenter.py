"""本地工作台的 Opcenter 连接测试和物料页面 API。"""
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

import config
from connectors.opcenter.client import OpcenterX509Client
from connectors.opcenter.materials import MaterialNotFound, MaterialService
from connectors.opcenter.x509 import CertificateError
from connectors.opcenter.creation_store import MaterialCreateError

router = APIRouter()


def service(request: Request) -> MaterialService:
    if request.app.state.opcenter is None:
        request.app.state.opcenter = MaterialService(OpcenterX509Client.from_config())
    return request.app.state.opcenter


async def call(operation):
    try:
        return await operation()
    except MaterialCreateError as exc:
        raise HTTPException(exc.status, exc.detail) from None
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


class CreateMaterialRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)
    request_id: UUID
    NId: StrictStr = Field(min_length=1, max_length=128)
    Name: StrictStr = Field(min_length=1, max_length=256)
    Revision: StrictStr = Field(min_length=1, max_length=64)
    UOM: StrictStr = Field(min_length=1, max_length=128)
    Description: StrictStr | None = Field(None, max_length=2000)
    MaterialClassNId: StrictStr | None = Field(None, max_length=128)
    SerialNumberProfile: StrictBool = False
    FirstArticleInspection: StrictBool = False
    Density: StrictFloat | None = Field(None, ge=0)
    MinQuantity: StrictFloat | None = Field(None, ge=0)
    MaxRecycleCount: StrictInt | None = Field(None, ge=0, le=2147483647)
    RevisionExpression: StrictStr | None = Field(None, max_length=1000)

    @field_validator('NId', 'Revision', 'UOM', 'MaterialClassNId')
    @classmethod
    def identifier(cls, value):
        if value is not None and any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('编码和版本不能包含控制字符。')
        return value


@router.get('/api/opcenter/material-creation-options')
async def creation_options(request: Request):
    return await call(lambda: service(request).creation_options())


@router.post('/api/opcenter/materials')
async def create_material(request: Request, body: CreateMaterialRequest):
    command = body.model_dump(exclude={'request_id'}, exclude_none=True)
    command = {key: value for key, value in command.items() if value != ''}
    result = await call(lambda: service(request).create_material(command, str(body.request_id)))
    return JSONResponse(result, status_code=201 if result['verified'] else 202)


@router.get('/api/opcenter/material-creations/{request_id}')
async def creation_status(request: Request, request_id: UUID):
    return await call(lambda: service(request).creation_status(str(request_id)))
