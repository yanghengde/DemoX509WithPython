"""UADM 标准创建命令、参考选项及提交后核实。"""
import asyncio
from uuid import UUID

import httpx

from connectors.opcenter.creation_store import MaterialCreateError

CREATE_ACTION = 'UADMCreateMaterialDefinition'


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def business_message(data):
    if not isinstance(data, dict):
        return None
    error = data.get('Error') or data.get('error')
    if isinstance(error, dict):
        message = error.get('ErrorMessage') or error.get('message')
        if isinstance(message, dict):
            message = message.get('value')
        if isinstance(message, str):
            return ' '.join(message.split())[:600]
    return None


class MaterialCreationMixin:
    async def creation_options(self):
        schema = await self.schema()
        if CREATE_ACTION not in schema['actions']:
            raise MaterialCreateError(409, 'command_unavailable', '当前 UADM 未发布创建物料命令。')
        result = {}
        for entity, key in [('UoM', 'units'), ('MaterialClass', 'classes')]:
            rows = []
            for _ in range(25):
                response = await self.client.request('GET', entity, params={
                    '$top': 200, '$skip': len(rows), '$count': 'true', '$select': 'Id,NId,Name', '$orderby': 'NId asc,Id asc'})
                data = self.collection(response)
                rows.extend(data['value'])
                count = data.get('@odata.count')
                if count is not None and len(rows) >= count:
                    break
                if not data['value'] or (count is None and len(data['value']) < 200 and not data.get('@odata.nextLink')):
                    break
            else:
                raise ValueError('创建选项超过读取上限，请缩小本机参考数据范围。')
            result[key] = rows
        return {**result, 'command': CREATE_ACTION}

    async def find_definition(self, command):
        response = await self.client.request('GET', 'DM_Material', params={
            '$top': 2, '$filter': f"Material/NId eq {literal(command['NId'])} and Material/Revision eq {literal(command['Revision'])}",
            '$expand': 'Material'})
        rows = self.collection(response)['value']
        if len(rows) > 1:
            raise ValueError('相同物料编码和版本对应多个 UADM 记录，请核对数据。')
        if not rows:
            return None
        item = self.linked_material(rows[0], include_uadm=True)
        if item.get('NId') != command['NId'] or item.get('Revision') != command['Revision']:
            raise ValueError('查询返回的物料编码或版本与创建请求不一致。')
        return item

    async def validate_creation(self, command):
        schema = await self.schema()
        if CREATE_ACTION not in schema['actions']:
            raise MaterialCreateError(409, 'command_unavailable', '当前 UADM 未发布创建物料命令。')
        for entity, field in [('UoM', 'UOM'), ('MaterialClass', 'MaterialClassNId')]:
            if not command.get(field):
                continue
            response = await self.client.request('GET', entity, params={
                '$top': 2, '$select': 'NId', '$filter': f'NId eq {literal(command[field])}'})
            rows = self.collection(response)['value']
            if len(rows) != 1 or rows[0].get('NId') != command[field]:
                raise MaterialCreateError(422, 'invalid_reference', '所选计量单位或物料类别已不存在，请重新加载选项。')

    def creation_record(self, request_id):
        record = self.creations.get(request_id)
        if record is None:
            raise MaterialCreateError(404, 'request_not_found', '未找到该创建请求。')
        if record['base_url'] != self.client.connection.base_url:
            raise MaterialCreateError(409, 'connection_changed', '该请求属于其他 Opcenter 连接，请核对服务地址。')
        return record

    @staticmethod
    def verified_item(item, command, command_id=None):
        if item is None:
            return False
        if command_id and command_id not in (item['Id'], item['DM_MaterialId']):
            return False
        return (item.get('Name') == command['Name'] and item.get('UoMNId') == command['UOM']
                and item['UADM'].get('SerialNumberProfile') == command['SerialNumberProfile']
                and item['UADM'].get('FirstArticleInspection') == command['FirstArticleInspection'])

    async def creation_status(self, request_id):
        record = self.creation_record(request_id)
        if record['state'] in ('created', 'rejected'):
            return record['result']
        result = record['result'] or {'ok': False, 'state': 'uncertain', 'request_id': request_id,
                                      'NId': record['command']['NId'], 'Revision': record['command']['Revision'],
                                      'verified': False, 'message': '创建结果尚未确认，请检查物料后再操作。'}
        item = await self.find_definition(record['command'])
        if self.verified_item(item, record['command'], result.get('command_id')):
            result = {**result, 'ok': True, 'state': 'created', 'verified': True, 'item': item,
                      'message': '已从 UADM 查询到匹配的物料，创建结果已核实。'}
            self.creations.save(request_id, 'created', result)
        return result

    async def create_material(self, command, request_id):
        request_id = str(UUID(str(request_id)))
        # Serialize the pre-check and submission in this local single-process app.
        async with self._creation_lock:
            previous = self.creations.get(request_id)
            if previous:
                if previous['fingerprint'] != self.creations.fingerprint(self.client.connection.base_url, command):
                    raise MaterialCreateError(409, 'request_changed', '同一请求标识不能用于不同的物料内容。')
                if previous['state'] == 'rejected':
                    result = previous['result']
                    raise MaterialCreateError(result['status'], result['code'], result['message'], request_id=request_id)
                if previous['state'] in ('created', 'accepted'):
                    return previous['result']
                raise MaterialCreateError(409, 'creation_uncertain', '该请求已提交，请先检查创建结果，不会重复发送。', request_id=request_id)
            await self.validate_creation(command)
            if await self.find_definition(command):
                raise MaterialCreateError(409, 'material_exists', '该物料编码和版本已存在，请使用其他编码或版本。')
            self.creations.reserve(request_id, self.client.connection.base_url, command)
            result = {'ok': False, 'state': 'uncertain', 'request_id': request_id,
                      'NId': command['NId'], 'Revision': command['Revision'], 'verified': False,
                      'message': '创建请求已发送，但结果尚未确认。请检查创建结果，不要重复提交。'}
            try:
                response = await self.client.request('POST', CREATE_ACTION, body={'command': command})
                try:
                    data = response.json()
                except ValueError:
                    data = None
                if isinstance(data, dict) and data.get('Succeeded') is False:
                    message = business_message(data) or 'Opcenter 未接受创建命令，请检查物料参数。'
                    raise MaterialCreateError(400, 'business_error', message)
                if not isinstance(data, dict) or data.get('Succeeded') is not True:
                    raise ValueError('创建响应未明确报告成功。')
                command_id = str(UUID(str(data.get('Id', ''))))
                result = {**result, 'ok': True, 'state': 'accepted', 'command_id': command_id,
                          'message': 'UADM 已确认创建成功，物料列表正在同步，可稍后检查创建结果。'}
                self.creations.save(request_id, 'accepted', result)
            except MaterialCreateError as exc:
                self.creations.save(request_id, 'rejected', {'ok': False, 'state': 'rejected',
                    'status': exc.status, **exc.detail, 'request_id': request_id})
                raise
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    try:
                        message = business_message(exc.response.json())
                    except ValueError:
                        message = None
                    message = message or {401: '证书身份认证失败，未能创建物料。',
                                          403: '当前证书角色没有创建物料权限。'}.get(exc.response.status_code, 'Opcenter 拒绝创建，请检查参数。')
                    self.creations.save(request_id, 'rejected', {'ok': False, 'state': 'rejected',
                        'status': exc.response.status_code, 'code': 'business_error', 'message': message, 'request_id': request_id})
                    raise MaterialCreateError(exc.response.status_code, 'business_error', message, request_id=request_id) from None
                self.creations.save(request_id, 'uncertain', result)
                raise MaterialCreateError(502, 'creation_uncertain', result['message'], request_id=request_id) from None
            except (httpx.RequestError, ValueError):
                self.creations.save(request_id, 'uncertain', result)
                raise MaterialCreateError(504, 'creation_uncertain', result['message'], request_id=request_id) from None
            # A successful command remains successful even when the read model is delayed.
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(0.5)
                try:
                    checked = await self.creation_status(request_id)
                    if checked['state'] == 'created':
                        return checked
                except (httpx.HTTPError, ValueError):
                    break
            return result
