import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import time
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
import httpx
import jwt
import pytest

from connectors.opcenter.client import OpcenterX509Client
from connectors.opcenter.materials import MaterialService
from connectors.opcenter.x509 import CertificateError, CertificateTokenProvider, X509Settings
from core.http_client import ProductConnection
from web.app import create_app

MATERIAL_ID = 'b6016adb-f759-4524-82f7-e63c2ab39027'
DM_MATERIAL_ID = 'b6016adb-f759-4524-82f7-e63c2ab39028'
METADATA = b'''<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0"><edmx:DataServices><Schema Namespace="Test" xmlns="http://docs.oasis-open.org/odata/ns/edm"><EntityType Name="Material"><Key><PropertyRef Name="Id"/></Key><Property Name="Id" Type="Edm.Guid"/><Property Name="NId" Type="Edm.String"/><Property Name="Name" Type="Edm.String"/><Property Name="Description" Type="Edm.String"/><Property Name="Revision" Type="Edm.String"/><Property Name="UoMNId" Type="Edm.String"/><Property Name="IsCurrent" Type="Edm.Boolean"/><NavigationProperty Name="Properties" Type="Collection(Test.Property)"/><NavigationProperty Name="MaterialGroups" Type="Collection(Test.Group)"/></EntityType><EntityContainer Name="Container"><EntitySet Name="Material" EntityType="Test.Material"/></EntityContainer></Schema></edmx:DataServices></edmx:Edmx>'''
LEGACY_METADATA = METADATA
METADATA = METADATA.replace(b'<EntityContainer', b'<EntityType Name="DM_Material"><Property Name="Id" Type="Edm.Guid"/><Property Name="Material_Id" Type="Edm.Guid"/><NavigationProperty Name="Material" Type="Test.Material"/><NavigationProperty Name="MaterialClass" Type="Test.MaterialClass"/><NavigationProperty Name="Supplier" Type="Test.Supplier"/></EntityType><EntityContainer')
METADATA = METADATA.replace(b'</EntityContainer>', b'<EntitySet Name="DM_Material" EntityType="Test.DM_Material"/></EntityContainer>')


def dm_row(material=None, **values):
    material = material or {'Id': MATERIAL_ID, 'NId': 'MAT-001'}
    return {'Id': DM_MATERIAL_ID, 'Material_Id': material['Id'], 'Material': material,
            'SerialNumberProfile': True, 'Traceable': False, **values}


@pytest.fixture
def certificate(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Integration Test'),
                      x509.NameAttribute(NameOID.TITLE, 'SimaticIT UA Foundation')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))
    path = tmp_path / 'integration.pfx'
    path.write_bytes(pkcs12.serialize_key_and_certificates(b'test', key, cert, None,
                                                         serialization.BestAvailableEncryption(b'test-password')))
    return X509Settings(source='pfx', pfx_path=str(path), pfx_password='test-password'), cert


def test_pfx_signature_claims_and_cache(certificate):
    settings, cert = certificate
    provider = CertificateTokenProvider(settings)
    token = provider.token()
    claims = jwt.decode(token, cert.public_key(), algorithms=['RS256'],
                        audience='urn:unified', issuer='urn:unifiedoauth')
    thumbprint = cert.fingerprint(hashes.SHA1()).hex().upper()
    assert claims['unique_name'] == 'T=SimaticIT UA Foundation, CN=Integration Test'
    assert claims['certthumbprint'] == thumbprint
    assert claims['urn:realm'] == 'x.509'
    assert claims['exp'] - claims['iat'] == 120
    header = jwt.get_unverified_header(token)
    assert header['kid'] == thumbprint
    assert header['x5t'] == base64.urlsafe_b64encode(cert.fingerprint(hashes.SHA1())).rstrip(b'=').decode()
    assert provider.token() == token
    provider._expires = int(time.time()) + 10
    provider.token()
    assert provider._expires > time.time() + 100
    assert 'test-password' not in repr(settings)


def test_bad_pfx_and_thumbprint_do_not_leak_password(certificate):
    settings, _ = certificate
    with pytest.raises(CertificateError, match='无法解密') as exc:
        CertificateTokenProvider(replace(settings, pfx_password='wrong-secret')).token()
    assert 'wrong-secret' not in str(exc.value)
    with pytest.raises(CertificateError, match='指纹不匹配'):
        CertificateTokenProvider(replace(settings, thumbprint='A' * 40)).token()


def make_service(handler):
    client = OpcenterX509Client(ProductConnection(base_url='http://opcenter.test/AppU4DM/odata'),
                               X509Settings(), transport=httpx.MockTransport(handler))
    client.tokens.token = lambda: 'test-only-token'
    return MaterialService(client)


def test_material_query_escapes_odata_search_and_bounds_requests():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer test-only-token'
        assert request.method == 'GET'
        if request.url.path.endswith('$metadata'):
            return httpx.Response(200, content=METADATA)
        assert request.url.path == '/AppU4DM/odata/DM_Material'
        return httpx.Response(200, json={'@odata.count': 25, 'value': [dm_row({'Id': MATERIAL_ID, 'NId': "O'Brien"})]})
    async def scenario():
        service = make_service(handler)
        try:
            result = await service.list_materials(keyword="O'Brien", top=10, skip=10, current_only=True)
            assert result['total'] == 25 and result['has_more']
            query = requests[-1].url.params
            assert query['$skip'] == '10' and query['$top'] == '10'
            assert query['$filter'] == "(contains(Material/NId,'O''Brien') or contains(Material/Name,'O''Brien') or contains(Material/Description,'O''Brien')) and Material/IsCurrent eq true"
            assert query['$orderby'] == 'Material/NId asc,Id asc'
            assert query['$expand'].startswith('Material($select=Id,NId,')
            assert result['entity'] == 'DM_Material'
            assert result['items'][0]['Id'] == MATERIAL_ID
            assert result['items'][0]['DM_MaterialId'] == DM_MATERIAL_ID
            await service.list_materials()
            assert sum(r.url.path.endswith('$metadata') for r in requests) == 1
            for values in [{'top': 101}, {'skip': -1}, {'keyword': 'x' * 121}]:
                with pytest.raises(ValueError):
                    await service.list_materials(**values)
        finally:
            await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('content', [b'<html>sign in</html>', b'not xml', b'<x/>'])
def test_connection_never_accepts_html_or_invalid_metadata(content):
    async def scenario():
        service = make_service(lambda request: httpx.Response(200, content=content))
        try:
            with pytest.raises(ValueError):
                await service.check_connection()
        finally:
            await service.close()
    asyncio.run(scenario())


def test_routes_real_read_flow_details_and_validation(monkeypatch):
    from web import opcenter
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith('$metadata'):
            return httpx.Response(200, content=METADATA)
        assert request.url.path == '/AppU4DM/odata/DM_Material'
        if request.url.params.get('$filter', '').startswith('Material_Id eq '):
            assert request.url.params['$filter'] == f'Material_Id eq {MATERIAL_ID}'
            assert request.url.params['$expand'] == 'Material($expand=Properties,MaterialGroups),MaterialClass,Supplier'
            return httpx.Response(200, json={'value': [dm_row({'Id': MATERIAL_ID, 'NId': 'MAT-001', 'Properties': [], 'MaterialGroups': []}, MaterialClass={'NId': 'finished-goods'})]})
        return httpx.Response(200, json={'@odata.count': 1, 'value': [dm_row()]})
    app = create_app()
    app.state.opcenter = make_service(handler)
    monkeypatch.setattr(opcenter.config, 'OPCENTER_X509_PFX_PASSWORD', 'must-not-be-exposed')
    with TestClient(app) as web:
        assert web.get('/opcenter').status_code == 200
        assert '/opcenter' in web.get('/').text
        config = web.get('/api/opcenter/config')
        assert 'must-not-be-exposed' not in config.text
        assert web.post('/api/opcenter/connection').json()['ok'] is True
        data = web.get('/api/opcenter/materials', params={'top': 10}).json()
        assert data['items'][0]['NId'] == 'MAT-001' and data['total'] == 1
        detail = web.get(f'/api/opcenter/materials/{MATERIAL_ID}').json()
        assert detail['Properties'] == []
        assert detail['DM_MaterialId'] == DM_MATERIAL_ID
        assert detail['UADM']['SerialNumberProfile'] is True
        assert detail['UADM']['MaterialClass']['NId'] == 'finished-goods'
        before = len(requests)
        assert web.get('/api/opcenter/materials/not-a-guid').status_code == 422
        assert web.get('/api/opcenter/materials?top=101').status_code == 422
        assert web.get('/api/opcenter/materials?skip=-1').status_code == 422
        assert web.get('/api/opcenter/materials', params={'keyword': 'x' * 121}).status_code == 422
        assert web.post('/api/opcenter/materials', json={}).status_code == 422
        assert len(requests) == before


@pytest.mark.parametrize('upstream,code,status', [(503, 'maintenance', 503), (401, 'upstream_error', 502),
                                                (403, 'upstream_error', 502), (404, 'upstream_error', 404)])
def test_upstream_error_diagnostics_without_raw_body(upstream, code, status):
    app = create_app()
    app.state.opcenter = make_service(lambda request: httpx.Response(upstream, text='Application is under maintenance SECRET-RAW-BODY'))
    with TestClient(app) as web:
        response = web.post('/api/opcenter/connection')
        assert response.status_code == status
        assert response.json()['detail']['code'] == code
        assert response.json()['detail']['upstream_status'] == upstream
        assert 'SECRET-RAW-BODY' not in response.text and 'test-only-token' not in response.text


def test_credentials_never_follow_redirect_or_external_next_link():
    sent = []
    def handler(request):
        sent.append(request)
        if request.url.path.endswith('$metadata'):
            return httpx.Response(200, content=METADATA)
        return httpx.Response(200, json={'value': [], '@odata.nextLink': 'https://external.test/collect'})
    async def scenario():
        service = make_service(handler)
        try:
            data = await service.list_materials()
            assert data['has_more']
            assert len(sent) == 2 and all(r.url.host == 'opcenter.test' for r in sent)
            with pytest.raises(ValueError):
                await service.client.request('GET', 'https://external.test/collect')
            assert len(sent) == 2
        finally:
            await service.close()
        service = make_service(lambda request: httpx.Response(302, headers={'location': 'https://external.test/collect'}))
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await service.schema()
        finally:
            await service.close()
    asyncio.run(scenario())


def test_uadm_rejects_legacy_material_service_contract():
    async def scenario():
        service = make_service(lambda request: httpx.Response(200, content=LEGACY_METADATA))
        try:
            with pytest.raises(ValueError, match='DM_Material'):
                await service.check_connection()
        finally:
            await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('rows,status,code', [([], 404, 'material_not_found'),
                                            ([dm_row(), dm_row()], 502, 'invalid_response'),
                                            ([{'Id': DM_MATERIAL_ID}], 502, 'invalid_response')])
def test_uadm_detail_missing_ambiguous_or_unlinked_material(rows, status, code):
    def handler(request):
        if request.url.path.endswith('$metadata'):
            return httpx.Response(200, content=METADATA)
        return httpx.Response(200, json={'value': rows})
    app = create_app()
    app.state.opcenter = make_service(handler)
    with TestClient(app) as web:
        response = web.get(f'/api/opcenter/materials/{MATERIAL_ID}')
        assert response.status_code == status
        assert response.json()['detail']['code'] == code
