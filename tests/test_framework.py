import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

from agent import llm_client, memory
from core.auth import authentication
from core.http_client import ProductClient, ProductConnection
from tools import mcp, get_tool_func
from web.app import create_app


def test_stdio_entrypoint_runs_without_web_or_product():
    async def scenario():
        transport = PythonStdioTransport(Path(__file__).resolve().parents[1] / 'mcp_server.py')
        async with Client(transport) as client:
            assert {tool.name for tool in await client.list_tools()} == {'get_capabilities'}
            result = await client.call_tool('get_capabilities', {})
            assert json.loads(result.content[0].text)['business_tool_count'] == 0
    asyncio.run(scenario())


def test_only_framework_capability_is_exposed_and_callable_through_mcp():
    async def scenario():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names={tool.name for tool in tools}
            assert names == {'get_capabilities'}
            result = await client.call_tool('get_capabilities', {})
            data = json.loads(result.content[0].text)
            assert data['status'] == 'framework_only'
            assert data['business_tool_count'] == 0
            assert data['business_capabilities'] == []
            detail = json.loads((await client.call_tool('get_capabilities', {'tool_name': 'get_capabilities'})).content[0].text)
            assert set(detail['tool']['parameters']['properties']) == {'group', 'tool_name'}
        assert get_tool_func('create_spec') is None
        assert get_tool_func('anything_else') is None
    asyncio.run(scenario())


def test_web_without_product_or_model_can_chat_and_recover_history():
    with TestClient(create_app()) as client:
        assert client.get('/health').json() == {'status': 'ok', 'mode': 'framework_only', 'agent_engine': 'langgraph'}
        assert client.get('/').status_code == 200
        assert client.get('/logs').status_code == 200
        assert client.get('/static/workspace.js').status_code == 200
        assert client.get('/static/vendor/marked-15.0.12.min.js').status_code == 200
        username = client.get('/config').json()['username']
        sid = client.post(f'/sessions/{username}/new').json()['session_id']
        response = client.post('/chat', json={'username': username, 'session_id': sid, 'message': '你有什么能力？'})
        assert response.status_code == 200
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert any(event['type'] == 'done' and '空框架' in event['reply'] for event in events)
        history = client.get(f'/history/{username}', params={'session_id': sid}).json()
        assert history['messages'][-1]['role'] == 'assistant'
        assert history['run']['status'] == 'completed'
        assert client.get(f'/sessions/{username}').json()['active_session'] == sid


def test_graph_calls_capability_and_emits_final_answer_with_mock_model(tmp_path, monkeypatch):
    from agent import langgraph_runtime as runtime
    async def scenario():
        await llm_client.register_tools()
        sid = memory.create_session('model-test')
        class Completions:
            rounds = 0
            async def create(self, **kwargs):
                self.rounds += 1
                async def stream():
                    if self.rounds == 1:
                        yield NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(index=0, id='call-1', function=NS(name='get_capabilities', arguments='{}'))]))], usage=None)
                    else:
                        assert json.loads(kwargs['messages'][-1]['content'])['business_tool_count'] == 0
                        yield NS(choices=[NS(delta=NS(content='当前仅有空框架。', tool_calls=None))], usage=None)
                return stream()
        mock = NS(chat=NS(completions=Completions()))
        monkeypatch.setattr(runtime, 'LANGGRAPH_CHECKPOINT_DB', str(tmp_path / 'graph.sqlite'))
        await runtime.init_langgraph_runtime(mock, llm_client.openai_tools)
        try:
            events = [event async for event in runtime.langgraph_chat_stream('model-test', '支持哪些能力？', sid)]
            assert any('get_capabilities' in event for event in events)
            assert any('当前仅有空框架' in event for event in events)
            assert (await runtime.get_workflow_status('model-test', sid))['status'] == 'complete'
        finally:
            await runtime.close_langgraph_runtime()
    asyncio.run(scenario())


def test_http_mcp_requires_token_and_can_list_only_capability(monkeypatch):
    from web import app as app_module
    monkeypatch.setattr(app_module, 'ENABLE_MCP_HTTP', True)
    monkeypatch.setattr(app_module, 'MCP_API_KEY', '')
    with pytest.raises(RuntimeError, match='MCP_API_KEY'):
        app_module.create_app()
    monkeypatch.setattr(app_module, 'MCP_API_KEY', 'test-only-secret')
    monkeypatch.setattr(app_module, 'MCP_ALLOWED_HOSTS', ['testserver'])
    with TestClient(app_module.create_app()) as client:
        assert client.post('/mcp/', json={}).status_code == 401
        headers = {'Authorization': 'Bearer test-only-secret', 'Accept': 'application/json, text/event-stream'}
        response = client.post('/mcp/', headers=headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
        assert response.status_code == 200, response.text
        assert response.json()['result']['serverInfo']['name'] == 'DemoFramework'
        headers['MCP-Protocol-Version'] = response.json()['result']['protocolVersion']
        result = client.post('/mcp/', headers=headers, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
        assert result.status_code == 200, result.text
        assert {tool['name'] for tool in result.json()['result']['tools']} == {'get_capabilities'}


def test_product_client_not_configured_does_not_send_request():
    async def scenario():
        def handler(request):
            pytest.fail('Unconfigured connection sent a request')
        client = ProductClient(ProductConnection(), transport=httpx.MockTransport(handler))
        with pytest.raises(ValueError, match='PRODUCT_BASE_URL'):
            await client.request('GET', '/status')
    asyncio.run(scenario())


@pytest.mark.parametrize('path', ['https://elsewhere.test/api', '//elsewhere.test/api', '../secret', '/%2e%2e/secret', '/api?x=1', '/a\\..\\secret'])
def test_product_client_rejects_paths_outside_product(path):
    client = ProductClient(ProductConnection(base_url='https://example.test/product/api'))
    with pytest.raises(ValueError):
        client.build_url(path)


def test_product_client_preserves_base_path_auth_and_never_retries_write():
    async def scenario():
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(503 if request.method == 'POST' else 200, json={'value': []})
        client = ProductClient(ProductConnection(base_url='https://example.test/product/api', auth_mode='bearer', token='test-token'), transport=httpx.MockTransport(handler))
        try:
            response = await client.request('GET', '/status', params={'top': 1})
            assert response.json() == {'value': []}
            assert str(requests[0].url) == 'https://example.test/product/api/status?top=1'
            assert requests[0].headers['Authorization'] == 'Bearer test-token'
            with pytest.raises(httpx.HTTPStatusError):
                await client.request('POST', 'objects', body={'name': 'test'})
            assert len(requests) == 2
        finally:
            await client.close()
    asyncio.run(scenario())


def test_confirmation_unknown_is_blocked_and_read_is_not():
    from agent.safety import MutationCounts, evaluate_mutations, plan_fingerprint
    read = {'function': {'name': 'get_capabilities', 'arguments': '{}'}}
    unknown = {'function': {'name': 'unregistered', 'arguments': '{}'}}
    assert not evaluate_mutations(MutationCounts(), [read]).requires_approval
    assert evaluate_mutations(MutationCounts(), [unknown], auto_confirm=True).requires_approval
    assert plan_fingerprint([read]) != plan_fingerprint([unknown])


def test_generic_auth_does_not_inherit_old_product_scheme():
    assert authentication('none') == ({}, None)
    headers, auth = authentication('basic', username='user', password='test-password')
    assert headers == {} and isinstance(auth, httpx.BasicAuth)
    assert authentication('api_key', api_key='test-key')[0] == {'X-API-Key': 'test-key'}
    with pytest.raises(ValueError):
        authentication('bearer')
    with pytest.raises(ValueError):
        authentication('unsupported')


def test_graph_confirmation_resumes_the_bound_call_once(tmp_path, monkeypatch):
    from agent import langgraph_runtime as runtime
    from core.tool_catalog import TOOL_POLICIES, ToolPolicy
    async def scenario():
        calls = []
        async def update_record(record_id: str):
            calls.append(record_id)
            return 'saved'
        class Completions:
            rounds = 0
            async def create(self, **kwargs):
                self.rounds += 1
                async def stream():
                    if self.rounds == 1:
                        yield NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(index=0, id='test-write', function=NS(name='update_record', arguments='{"record_id":"chosen-record"}'))]))], usage=None)
                    else:
                        yield NS(choices=[NS(delta=NS(content='已完成。', tool_calls=None))], usage=None)
                return stream()
        monkeypatch.setitem(TOOL_POLICIES, 'update_record', ToolPolicy('test', 'update', always_confirm=True))
        monkeypatch.setattr(runtime, 'get_tool_func', lambda name: update_record if name == 'update_record' else None)
        monkeypatch.setattr(runtime, 'LANGGRAPH_CHECKPOINT_DB', str(tmp_path / 'approval.sqlite'))
        tool = {'type': 'function', 'function': {'name': 'update_record', 'description': 'Test-only update',
                'parameters': {'type': 'object', 'properties': {'record_id': {'type': 'string'}}, 'required': ['record_id']}}}
        sid = memory.create_session('approval-test')
        await runtime.init_langgraph_runtime(NS(chat=NS(completions=Completions())), [tool])
        try:
            [event async for event in runtime.langgraph_chat_stream('approval-test', '更新指定记录', sid)]
            state = await runtime.get_workflow_status('approval-test', sid)
            assert state['status'] == 'approval_required'
            assert calls == []
            assert 'chosen-record' in state['approval']['execution_details']
            [event async for event in runtime.langgraph_chat_stream('approval-test', '确认执行', sid)]
            assert calls == ['chosen-record']
            assert (await runtime.get_workflow_status('approval-test', sid))['status'] == 'complete'
        finally:
            await runtime.close_langgraph_runtime()
    asyncio.run(scenario())
