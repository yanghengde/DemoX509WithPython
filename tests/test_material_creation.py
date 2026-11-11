import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from connectors.opcenter.creation_store import CreationStore, MaterialCreateError
from test_opcenter import METADATA, MATERIAL_ID, DM_MATERIAL_ID, dm_row, make_service
from web.app import create_app

CREATE_METADATA = METADATA.replace(b'</Schema>', b'<Action Name="UADMCreateMaterialDefinition"/></Schema>')


def payload(**changes):
    return {'request_id': str(uuid4()), 'NId': 'MAT-CREATE', 'Name': '新物料', 'Revision': 'A',
            'UOM': 'pce', 'SerialNumberProfile': False, 'FirstArticleInspection': False, **changes}


class UadmMock:
    def __init__(self):
        self.requests = []
        self.command = None
        self.visible = False
        self.response_mode = 'success'
        self.initially_exists = False

    def __call__(self, request):
        self.requests.append(request)
        assert request.headers['Authorization'] == 'Bearer test-only-token'
        if request.url.path.endswith('$metadata'):
            return httpx.Response(200, content=CREATE_METADATA)
        if request.method == 'POST':
            assert request.url.path == '/AppU4DM/odata/UADMCreateMaterialDefinition'
            body = json.loads(request.content)
            assert set(body) == {'command'}
            self.command = body['command']
            assert 'request_id' not in self.command
            if self.response_mode == 'timeout':
                self.visible = True
                raise httpx.ReadTimeout('test transport timeout', request=request)
            if self.response_mode == 'business_failure':
                return httpx.Response(200, json={'Succeeded': False, 'Error': {'ErrorCode': 42, 'ErrorMessage': '物料编码不可用'}})
            if self.response_mode == 'forbidden':
                return httpx.Response(403, text='<html>SECRET BODY</html>')
            if self.response_mode == 'html':
                return httpx.Response(200, text='<html>Sign in</html>')
            if self.response_mode == 'server_error':
                return httpx.Response(500, text='SECRET BODY')
            self.visible = self.response_mode != 'delayed'
            return httpx.Response(200, json={'Succeeded': True, 'Id': DM_MATERIAL_ID, 'Error': None})
        if request.url.path.endswith('/UoM'):
            return httpx.Response(200, json={'@odata.count': 1, 'value': [{'NId': 'pce', 'Name': 'Piece'}]})
        if request.url.path.endswith('/MaterialClass'):
            return httpx.Response(200, json={'@odata.count': 1, 'value': [{'NId': 'n/a', 'Name': 'n/a'}]})
        assert request.url.path.endswith('/DM_Material')
        items = []
        if self.visible or self.initially_exists:
            command = self.command or payload()
            material = {'Id': MATERIAL_ID, 'NId': command['NId'], 'Revision': command['Revision'],
                        'Name': command['Name'], 'UoMNId': command['UOM']}
            items = [dm_row(material, SerialNumberProfile=command['SerialNumberProfile'],
                            FirstArticleInspection=command['FirstArticleInspection'])]
        return httpx.Response(200, json={'value': items, '@odata.count': len(items)})

    @property
    def posts(self):
        return [r for r in self.requests if r.method == 'POST']


def test_uadm_create_verified_replay_and_restart(tmp_path):
    backend = UadmMock()
    store = tmp_path / 'creations.sqlite'
    app = create_app()
    app.state.opcenter = make_service(backend)
    app.state.opcenter.creations = CreationStore(store)
    body = payload(NId=" MAT'O-001 ", Name=' 新物料 ', Description=' 描述 ', MaterialClassNId='n/a')
    with TestClient(app) as web:
        response = web.post('/api/opcenter/materials', json=body)
        assert response.status_code == 201, response.text
        result = response.json()
        assert result['verified'] and result['state'] == 'created'
        assert result['item']['DM_MaterialId'] == DM_MATERIAL_ID
        assert backend.command['NId'] == "MAT'O-001" and backend.command['Name'] == '新物料'
        assert backend.command['Description'] == '描述' and backend.command['MaterialClassNId'] == 'n/a'
        assert "Material/NId eq 'MAT''O-001'" in backend.posts[0].url.path or any(
            "Material/NId eq 'MAT''O-001'" in r.url.params.get('$filter', '') for r in backend.requests)
        assert web.post('/api/opcenter/materials', json=body).json() == result
        assert len(backend.posts) == 1
        changed = {**body, 'Name': '修改后名称'}
        assert web.post('/api/opcenter/materials', json=changed).status_code == 409
        assert web.post('/api/opcenter/materials', json={**body, 'request_id': str(uuid4())}).json()['detail']['code'] == 'material_exists'
        assert len(backend.posts) == 1
    app = create_app()
    app.state.opcenter = make_service(backend)
    app.state.opcenter.creations = CreationStore(store)
    with TestClient(app) as web:
        assert web.post('/api/opcenter/materials', json=body).json() == result
        assert len(backend.posts) == 1


@pytest.mark.parametrize('changes', [{'NId': '  '}, {'Name': ''}, {'Revision': ''}, {'UOM': ''},
                                   {'SerialNumberProfile': 'true'}, {'FirstArticleInspection': 1},
                                   {'Density': -1}, {'Density': True}, {'MinQuantity': 'NaN'},
                                   {'MaxRecycleCount': 1.5}, {'MaxRecycleCount': -1},
                                   {'NId': 'bad\ncode'}, {'Name': 'x' * 257}, {'UnknownCommand': 'anything'}])
def test_create_validation_never_sends_command(changes):
    backend = UadmMock()
    app = create_app()
    app.state.opcenter = make_service(backend)
    with TestClient(app) as web:
        assert web.post('/api/opcenter/materials', json=payload(**changes)).status_code == 422
        assert not backend.requests


def test_invalid_reference_and_existing_material_never_write():
    backend = UadmMock()
    app = create_app()
    app.state.opcenter = make_service(backend)
    with TestClient(app) as web:
        assert web.post('/api/opcenter/materials', json=payload(UOM='missing-unit')).status_code == 422
        backend.initially_exists = True
        response = web.post('/api/opcenter/materials', json=payload())
        assert response.status_code == 409 and response.json()['detail']['code'] == 'material_exists'
        assert not backend.posts


@pytest.mark.parametrize('mode,status,code', [('business_failure', 400, 'business_error'),
                                           ('forbidden', 403, 'business_error'),
                                           ('html', 504, 'creation_uncertain'),
                                           ('server_error', 502, 'creation_uncertain')])
def test_create_business_failure_and_uncertain_results_are_never_replayed(tmp_path, mode, status, code):
    backend = UadmMock()
    backend.response_mode = mode
    app = create_app()
    app.state.opcenter = make_service(backend)
    app.state.opcenter.creations = CreationStore(tmp_path / 'requests.sqlite')
    body = payload()
    with TestClient(app) as web:
        response = web.post('/api/opcenter/materials', json=body)
        assert response.status_code == status, response.text
        assert response.json()['detail']['code'] == code
        assert 'SECRET BODY' not in response.text
        repeated = web.post('/api/opcenter/materials', json=body)
        assert repeated.status_code in (status, 409)
        assert len(backend.posts) == 1


def test_timeout_recovery_after_restart_is_read_only(tmp_path):
    backend = UadmMock()
    backend.response_mode = 'timeout'
    app = create_app()
    app.state.opcenter = make_service(backend)
    store = tmp_path / 'requests.sqlite'
    app.state.opcenter.creations = CreationStore(store)
    body = payload()
    with TestClient(app) as web:
        assert web.post('/api/opcenter/materials', json=body).json()['detail']['code'] == 'creation_uncertain'
    app = create_app()
    app.state.opcenter = make_service(backend)
    app.state.opcenter.creations = CreationStore(store)
    with TestClient(app) as web:
        assert web.post('/api/opcenter/materials', json=body).status_code == 409
        response = web.get('/api/opcenter/material-creations/' + body['request_id'])
        assert response.json()['state'] == 'created' and response.json()['verified']
        assert len(backend.posts) == 1
        assert web.post('/api/opcenter/materials', json=body).status_code == 201
        assert len(backend.posts) == 1


def test_successful_command_with_delayed_read_model_returns_accepted(tmp_path):
    backend = UadmMock()
    backend.response_mode = 'delayed'
    app = create_app()
    app.state.opcenter = make_service(backend)
    app.state.opcenter.creations = CreationStore(tmp_path / 'requests.sqlite')
    body = payload()
    with TestClient(app) as web:
        response = web.post('/api/opcenter/materials', json=body)
        assert response.status_code == 202 and response.json()['state'] == 'accepted'
        assert response.json()['ok'] and not response.json()['verified']
        assert web.post('/api/opcenter/materials', json=body).status_code == 202
        backend.visible = True
        assert web.get('/api/opcenter/material-creations/' + body['request_id']).json()['verified']
        assert len(backend.posts) == 1


def test_creation_options_handle_server_paging_and_status_ids():
    backend = UadmMock()
    skips = []
    def handler(request):
        if request.url.path.endswith('/UoM'):
            skip = int(request.url.params['$skip'])
            skips.append(skip)
            return httpx.Response(200, json={'@odata.count': 169, 'value': [
                {'NId': f'UNIT-{i}', 'Name': str(i)} for i in range(skip, min(skip + 50, 169))]})
        return backend(request)
    app = create_app()
    app.state.opcenter = make_service(handler)
    with TestClient(app) as web:
        response = web.get('/api/opcenter/material-creation-options')
        assert response.status_code == 200, response.text
        assert len(response.json()['units']) == 169
        assert skips == [0, 50, 100, 150]
        assert response.json()['command'] == 'UADMCreateMaterialDefinition'
        assert web.get('/api/opcenter/material-creations/not-a-uuid').status_code == 422
        assert web.get('/api/opcenter/material-creations/' + str(uuid4())).status_code == 404
        assert not backend.posts
