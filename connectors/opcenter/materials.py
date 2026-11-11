"""固定 Material 实体的只读查询；不接受任意 URL 或 OData 表达式。"""
import asyncio
import time
from uuid import UUID
from xml.etree import ElementTree as ET

from connectors.opcenter.client import OpcenterX509Client

EDM = '{http://docs.oasis-open.org/odata/ns/edm}'
EDMX = '{http://docs.oasis-open.org/odata/ns/edmx}'
LIST_FIELDS = ('Id', 'NId', 'Name', 'Description', 'Revision', 'UoMNId', 'IsCurrent', 'LastUpdatedOn')


class MaterialService:
    def __init__(self, client: OpcenterX509Client):
        self.client = client
        self._schema = None
        self._schema_at = 0
        self._schema_lock = asyncio.Lock()

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
            self._schema = {'entity_set_count': len(sets), 'fields': fields,
                            'navigation': sorted(navigation), 'metadata_bytes': len(response.content)}
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

    async def check_connection(self):
        started = time.perf_counter()
        schema = await self.schema(refresh=True)
        response = await self.client.request('GET', 'Material', params={'$top': 1, '$select': 'Id,NId'})
        data = self.collection(response)
        return {'ok': True, 'authentication': 'X.509 / RS256 Bearer JWT',
                'metadata_status': 200, 'materials_status': response.status_code,
                'entity_set_count': schema['entity_set_count'], 'sample_rows': len(data['value']),
                'elapsed_ms': round((time.perf_counter() - started) * 1000),
                'message': '证书认证、元数据和物料读取均成功。'}

    async def list_materials(self, *, keyword='', top=20, skip=0, current_only=False):
        if not 1 <= top <= 100 or not 0 <= skip <= 1_000_000 or len(keyword) > 120:
            raise ValueError('查询范围无效：每页 1–100 条，搜索最多 120 字。')
        schema = await self.schema()
        fields = schema['fields']
        params = {'$top': top, '$skip': skip, '$count': 'true',
                  '$select': ','.join(f for f in LIST_FIELDS if f in fields), '$orderby': 'NId asc,Id asc'}
        filters = []
        if keyword.strip():
            literal = keyword.strip().replace("'", "''")
            searchable = [f for f in ('NId', 'Name', 'Description') if fields.get(f) == 'Edm.String']
            filters.append('(' + ' or '.join(f"contains({f},'{literal}')" for f in searchable) + ')')
        if current_only:
            if fields.get('IsCurrent') != 'Edm.Boolean':
                raise ValueError('当前部署不支持按当前版本筛选。')
            filters.append('IsCurrent eq true')
        if filters:
            params['$filter'] = ' and '.join(filters)
        response = await self.client.request('GET', 'Material', params=params)
        data = self.collection(response)
        total = data.get('@odata.count')
        # Never follow a server-supplied nextLink with a signed credential.
        return {'items': data['value'], 'total': total, 'top': top, 'skip': skip,
                'has_more': skip + len(data['value']) < total if total is not None
                            else bool(data.get('@odata.nextLink')) or len(data['value']) == top}

    async def material(self, material_id: UUID):
        material_id = UUID(str(material_id))
        schema = await self.schema()
        expand = [p for p in ('Properties', 'MaterialGroups') if p in schema['navigation']]
        response = await self.client.request('GET', f'Material({material_id})',
                                             params={'$expand': ','.join(expand)} if expand else None)
        try:
            data = response.json()
        except ValueError:
            raise ValueError('物料详情未返回有效 JSON。') from None
        if not isinstance(data, dict) or str(data.get('Id', '')).lower() != str(material_id):
            raise ValueError('物料详情的 Id 与请求不一致。')
        return data

    async def close(self):
        await self.client.close()
