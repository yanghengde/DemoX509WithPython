"""UADM / AppU4DM 的 DM_Material 只读查询，关联 Material 基础字段。"""
import asyncio
import time
from uuid import UUID
from xml.etree import ElementTree as ET

from connectors.opcenter.client import OpcenterX509Client
from connectors.opcenter.creation_store import CreationStore
from connectors.opcenter.material_creation import MaterialCreationMixin

EDM = '{http://docs.oasis-open.org/odata/ns/edm}'
EDMX = '{http://docs.oasis-open.org/odata/ns/edmx}'
LIST_FIELDS = ('Id', 'NId', 'Name', 'Description', 'Revision', 'UoMNId', 'IsCurrent', 'LastUpdatedOn')


class MaterialNotFound(ValueError):
    pass


class MaterialService(MaterialCreationMixin):
    def __init__(self, client: OpcenterX509Client):
        self.client = client
        self._schema = None
        self._schema_at = 0
        self._schema_lock = asyncio.Lock()
        self._creation_lock = asyncio.Lock()
        import config
        self.creations = CreationStore(config.DATA_DIR / 'material_creations.sqlite')

    async def schema(self, *, refresh=False):
        async with self._schema_lock:
            if self._schema and not refresh and time.monotonic() - self._schema_at < 300:
                return self._schema
            response = await self.client.request('GET', '$metadata')
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError:
                raise ValueError('服务未返回有效的 OData XML 元数据，请检查服务地址。') from None
            if root.tag != EDMX + 'Edmx':
                raise ValueError('响应不是 OData EDMX 元数据，可能返回了登录页面。')
            sets = {n.get('Name'): n.get('EntityType') for n in root.iter(EDM + 'EntitySet')}
            entity_type = sets.get('Material')
            types = {s.get('Namespace') + '.' + t.get('Name'): t
                     for s in root.iter(EDM + 'Schema') for t in s.findall(EDM + 'EntityType')}
            if not entity_type or entity_type not in types:
                raise ValueError('当前服务的元数据中没有 Material 物料实体。')
            fields, navigation, visited = {}, set(), set()
            while entity_type in types and entity_type not in visited:
                visited.add(entity_type)
                node = types[entity_type]
                fields.update({p.get('Name'): p.get('Type') for p in node.findall(EDM + 'Property')})
                navigation.update(p.get('Name') for p in node.findall(EDM + 'NavigationProperty'))
                entity_type = node.get('BaseType')
            if fields.get('Id') != 'Edm.Guid' or fields.get('NId') != 'Edm.String':
                raise ValueError('Material 实体缺少预期的 Id / NId 字段，请核对部署契约。')
            dm_type = types.get(sets.get('DM_Material'))
            if dm_type is None:
                raise ValueError('当前服务没有 UADM 的 DM_Material 实体，请使用 AppU4DM/odata 服务地址。')
            dm_fields = {p.get('Name'): p.get('Type') for p in dm_type.findall(EDM + 'Property')}
            dm_navigation = {p.get('Name'): p.get('Type') for p in dm_type.findall(EDM + 'NavigationProperty')}
            if dm_fields.get('Material_Id') != 'Edm.Guid' or dm_navigation.get('Material') != sets['Material']:
                raise ValueError('UADM 的 DM_Material 与 Material 关联契约不匹配。')
            self._schema = {'entity_set_count': len(sets), 'fields': fields,
                            'navigation': sorted(navigation), 'dm_navigation': sorted(dm_navigation),
                            'actions': sorted({n.get('Name') for n in root.iter(EDM + 'Action') if n.get('Name')}),
                            'metadata_bytes': len(response.content)}
            self._schema_at = time.monotonic()
            return self._schema

    @staticmethod
    def collection(response):
        try:
            data = response.json()
        except ValueError:
            raise ValueError('物料接口未返回 JSON，可能返回了登录页面或代理错误。') from None
        if not isinstance(data, dict) or not isinstance(data.get('value'), list) or any(not isinstance(r, dict) for r in data['value']):
            raise ValueError('物料接口未返回有效的 OData value 数组。')
        count = data.get('@odata.count')
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError('物料接口返回的总数无效。')
        return data

    @staticmethod
    def linked_material(row, *, include_uadm=False):
        material = row.get('Material')
        if not isinstance(material, dict) or not material.get('Id') or not row.get('Id') or material['Id'] != row.get('Material_Id'):
            raise ValueError('UADM 物料缺少有效的 Material 关联，无法显示完整物料内容。')
        result = {**material, 'DM_MaterialId': row['Id']}
        if include_uadm:
            result['UADM'] = {key: value for key, value in row.items() if key != 'Material' and not key.startswith('@')}
        return result

    async def check_connection(self):
        started = time.perf_counter()
        schema = await self.schema(refresh=True)
        response = await self.client.request('GET', 'DM_Material', params={
            '$top': 1, '$select': 'Id,Material_Id', '$expand': 'Material($select=Id,NId)'})
        data = self.collection(response)
        for row in data['value']:
            self.linked_material(row)
        return {'ok': True, 'authentication': 'X.509 / RS256 Bearer JWT',
                'module': 'AppU4DM', 'entity': 'DM_Material',
                'metadata_status': 200, 'materials_status': response.status_code,
                'entity_set_count': schema['entity_set_count'], 'sample_rows': len(data['value']),
                'elapsed_ms': round((time.perf_counter() - started) * 1000),
                'message': '证书认证、UADM 元数据和 DM_Material 物料读取均成功。'}

    async def list_materials(self, *, keyword='', top=20, skip=0, current_only=False):
        if not 1 <= top <= 100 or not 0 <= skip <= 1_000_000 or len(keyword) > 120:
            raise ValueError('查询范围无效：每页 1–100 条，搜索最多 120 字。')
        schema = await self.schema()
        fields = schema['fields']
        params = {'$top': top, '$skip': skip, '$count': 'true',
                  '$select': 'Id,Material_Id',
                  '$expand': 'Material($select=' + ','.join(f for f in LIST_FIELDS if f in fields) + ')',
                  '$orderby': 'Material/NId asc,Id asc'}
        filters = []
        if keyword.strip():
            literal = keyword.strip().replace("'", "''")
            searchable = [f for f in ('NId', 'Name', 'Description') if fields.get(f) == 'Edm.String']
            filters.append('(' + ' or '.join(f"contains(Material/{f},'{literal}')" for f in searchable) + ')')
        if current_only:
            if fields.get('IsCurrent') != 'Edm.Boolean':
                raise ValueError('当前部署不支持按当前版本筛选。')
            filters.append('Material/IsCurrent eq true')
        if filters:
            params['$filter'] = ' and '.join(filters)
        response = await self.client.request('GET', 'DM_Material', params=params)
        data = self.collection(response)
        items = [self.linked_material(row) for row in data['value']]
        total = data.get('@odata.count')
        # Never follow a server-supplied nextLink with a signed credential.
        return {'items': items, 'total': total, 'top': top, 'skip': skip,
                'module': 'AppU4DM', 'entity': 'DM_Material',
                'has_more': skip + len(data['value']) < total if total is not None
                            else bool(data.get('@odata.nextLink')) or len(data['value']) == top}

    async def material(self, material_id: UUID):
        material_id = UUID(str(material_id))
        schema = await self.schema()
        nested = [p for p in ('Properties', 'MaterialGroups') if p in schema['navigation']]
        expand = ['Material($expand=' + ','.join(nested) + ')' if nested else 'Material']
        expand += [p for p in ('MaterialClass', 'Supplier', 'FunctionalCode') if p in schema['dm_navigation']]
        response = await self.client.request('GET', 'DM_Material', params={
            '$top': 2, '$filter': f'Material_Id eq {material_id}', '$expand': ','.join(expand)})
        rows = self.collection(response)['value']
        if not rows:
            raise MaterialNotFound('在 UADM 的 DM_Material 中未找到该物料。')
        if len(rows) != 1:
            raise ValueError('同一 Material 对应多个 UADM 物料记录，请核对部署数据。')
        result = self.linked_material(rows[0], include_uadm=True)
        if str(result['Id']).lower() != str(material_id):
            raise ValueError('UADM 物料详情的 Id 与请求不一致。')
        return result

    async def close(self):
        await self.client.close()
