'use strict';
const $ = id => document.getElementById(id);
let busy = false, result = null, offset = 0, detail = null, detailRequest = null, lastQuery = '';
const formatTime = value => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', {hour12: false});
};
async function api(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', ...options});
  let data;
  try { data = await response.json(); } catch { throw new Error(`应用返回了无效响应（HTTP ${response.status}）`); }
  if (!response.ok) {
    const info = data.detail;
    const error = new Error(typeof info === 'object' && !Array.isArray(info) ? info.message : '请求参数无效，请检查后重试。');
    error.status = response.status;
    error.upstream = info?.upstream_status;
    error.code = info?.code;
    error.requestId = info?.request_id;
    throw error;
  }
  return data;
}
function setStatus(kind, title, message) {
  $('statusDot').className = `dot ${kind}`;
  $('statusTitle').textContent = title;
  $('statusMessage').textContent = message;
}
function lock(value) {
  busy = value;
  document.querySelectorAll('#searchForm input, #searchForm button, #searchForm select, #testConnection, #refresh, #openCreate').forEach(el => { el.disabled = value; });
  $('export').disabled = value || !result?.items.length;
  $('previous').disabled = value || !result || offset === 0;
  $('next').disabled = value || !result?.has_more;
  $('rows').setAttribute('aria-busy', String(value));
}
function empty(title, message) {
  $('empty').hidden = false;
  $('emptyTitle').textContent = title;
  $('emptyMessage').textContent = message;
}
function clearResults() {
  result = null;
  $('rows').replaceChildren();
  $('totalCount').textContent = '待查询';
  $('range').textContent = '尚未查询';
  $('pageNumber').textContent = '第 1 页';
}
function showError(error) {
  clearResults();
  $('errorPanel').hidden = false;
  $('errorTitle').textContent = error.code === 'maintenance' ? 'Opcenter 处于维护状态' : '暂时无法读取物料';
  $('errorMessage').textContent = error.message || '网络请求失败，请重新测试连接。';
  $('errorCode').textContent = [error.status ? `应用 HTTP ${error.status}` : '', error.upstream ? `Opcenter HTTP ${error.upstream}` : '', error.code || ''].filter(Boolean).join(' · ');
  empty('物料尚未读取', '问题解决后，点击“测试连接”或“刷新”重新读取。');
  setStatus('error', error.code === 'maintenance' ? '服务维护中' : '连接或查询未成功', error.message);
}
function render(data) {
  result = data;
  const fragment = document.createDocumentFragment();
  for (const item of data.items) {
    const tr = document.createElement('tr');
    for (const field of ['NId', 'Name', 'Revision', 'UoMNId', 'IsCurrent', 'LastUpdatedOn']) {
      const td = document.createElement('td');
      const value = item[field];
      if (field === 'NId') {
        const link = document.createElement('button');
        link.type = 'button'; link.className = 'material-link'; link.textContent = value || item.Id;
        link.addEventListener('click', () => openDetail(item)); td.append(link);
      } else if (field === 'IsCurrent' && typeof value === 'boolean') {
        const badge = document.createElement('span'); badge.className = value ? 'badge' : 'badge inactive';
        badge.textContent = value ? '当前版本' : '历史版本'; td.append(badge);
      } else {
        td.textContent = field === 'LastUpdatedOn' ? formatTime(value) : (value ?? '—');
        td.title = td.textContent;
      }
      tr.append(td);
    }
    const td = document.createElement('td'), button = document.createElement('button');
    button.type = 'button'; button.className = 'detail-link'; button.textContent = '详情 →';
    button.addEventListener('click', () => openDetail(item)); td.append(button); tr.append(td); fragment.append(tr);
  }
  $('rows').replaceChildren(fragment);
  $('totalCount').textContent = data.total === null ? `本页 ${data.items.length} 条` : `${data.total.toLocaleString()} 条`;
  $('pageNumber').textContent = `第 ${Math.floor(offset / data.top) + 1} 页`;
  $('range').textContent = data.items.length ? `显示 ${offset + 1}–${offset + data.items.length} 条${data.total === null ? '' : `，共 ${data.total.toLocaleString()} 条`}` : '当前查询没有物料';
  $('empty').hidden = data.items.length > 0;
  if (!data.items.length) empty('没有找到物料', '请尝试其他关键词，或取消“仅当前版本”筛选。');
}
async function load(skip = 0) {
  if (busy) return;
  const query = JSON.stringify([$('keyword').value.trim(), $('pageSize').value, $('currentOnly').checked]);
  if (query !== lastQuery) skip = 0;
  lastQuery = query;
  lock(true); offset = skip; $('errorPanel').hidden = true; clearResults();
  empty('正在读取物料…', '通过 X.509 认证访问本地服务');
  const params = new URLSearchParams({keyword: $('keyword').value.trim(), top: $('pageSize').value, skip: String(skip), current_only: String($('currentOnly').checked)});
  try {
    const data = await api(`/api/opcenter/materials?${params}`); render(data);
    setStatus('ready', 'UADM 物料读取成功', '已通过证书认证，数据来自 AppU4DM 的 DM_Material 接口。');
  } catch (error) { showError(error); } finally { lock(false); }
}
async function testConnection() {
  if (busy) return;
  lock(true); clearResults(); $('errorPanel').hidden = true;
  setStatus('checking', '正在测试连接', '正在验证证书签名、元数据和物料读取权限…');
  empty('正在测试连接…', '验证成功后自动读取第一页物料。');
  let ok = false;
  try {
    const data = await api('/api/opcenter/connection', {method: 'POST'});
    setStatus('ready', '连接验证成功', `${data.message} 检测耗时 ${data.elapsed_ms.toLocaleString()} ms。`); ok = true;
  } catch (error) { showError(error); } finally {
    $('checkedAt').textContent = new Date().toLocaleTimeString('zh-CN', {hour12: false}); lock(false);
  }
  if (ok) await load(0);
}
function appendSection(title, value) {
  const heading = document.createElement('h3'); heading.textContent = title;
  const content = document.createElement('pre'); content.textContent = JSON.stringify(value, null, 2);
  $('detailContent').append(heading, content);
}
function appendUadm(value) {
  const heading = document.createElement('h3'); heading.textContent = 'UADM 物料配置 · DM_Material';
  const dl = document.createElement('dl');
  const labels = {SerialNumberProfile:'序列号管理', Traceable:'是否可追溯', Lot:'按批次管理',
    FirstArticleInspection:'首件检验', IsPhantom:'虚拟件', LogisticClassNId:'物流分类',
    EnableAutomaticConsumptionQty:'自动消耗数量', MaxQuantityConsumption:'最大消耗数量',
    Density:'密度', EffectivityExpression:'生效表达式', MaterialClass:'物料类别', Supplier:'供应商',
    FunctionalCode:'功能代码', Weight:'重量', Volume:'体积'};
  for (const [key, label] of Object.entries(labels)) {
    if (!(key in value)) continue;
    const div = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = label;
    let field = value[key];
    if (field && typeof field === 'object') {
      field = 'QuantityValue' in field ? (field.QuantityValue == null ? null : `${field.QuantityValue} ${field.UoMNId || ''}`.trim()) : (field.Name || field.NId);
    }
    dd.textContent = field == null ? '—' : typeof field === 'boolean' ? (field ? '是' : '否') : String(field);
    div.append(dt, dd); dl.append(div);
  }
  $('detailContent').append(heading, dl);
}
async function openDetail(item) {
  detailRequest?.abort(); detailRequest = new AbortController();
  const controller = detailRequest; detail = null;
  $('detailTitle').textContent = item.NId || '物料详情'; $('detailContent').textContent = '正在读取物料详情…';
  $('copyDetail').disabled = true; $('copyStatus').textContent = '';
  if (!$('detailDialog').open) $('detailDialog').showModal();
  try {
    const data = await api(`/api/opcenter/materials/${encodeURIComponent(item.Id)}`, {signal: controller.signal});
    if (controller.signal.aborted) return;
    detail = data; $('detailContent').replaceChildren();
    const dl = document.createElement('dl');
    const labels = {Id: '基础物料 Id', DM_MaterialId:'UADM 物料 Id', NId: '物料编码', Name: '物料名称', Description: '描述', Revision: '版本', SourceRevision: '来源版本', UoMNId: '计量单位', IsCurrent: '是否当前版本', CreatedOn: '创建时间', LastUpdatedOn: '更新时间', UId: 'UId', TemplateNId: '模板编码'};
    for (const [key, value] of Object.entries(data)) {
      if (key.startsWith('@') || (value !== null && typeof value === 'object')) continue;
      const div = document.createElement('div'); if (key === 'Id' || key === 'DM_MaterialId' || key === 'Description') div.className = 'wide';
      const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = labels[key] || key;
      dd.textContent = value === null ? '—' : typeof value === 'boolean' ? (value ? '是' : '否') : key.endsWith('On') ? formatTime(value) : String(value);
      div.append(dt, dd); dl.append(div);
    }
    $('detailContent').append(dl);
    for (const [key, value] of Object.entries(data)) {
      if (key === 'UADM' && value) appendUadm(value);
      else if (value !== null && typeof value === 'object') appendSection(key === 'Properties' ? '物料属性 · Properties' : key === 'MaterialGroups' ? '所属分组 · MaterialGroups' : key, value);
    }
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = '查看完整原始 JSON'; pre.textContent = JSON.stringify(data, null, 2); details.append(summary, pre); $('detailContent').append(details);
    $('copyDetail').disabled = false;
  } catch (error) {
    if (error.name === 'AbortError') return;
    $('detailContent').replaceChildren();
    const p = document.createElement('p'); p.className = 'detail-error'; p.textContent = error.message || '详情读取失败，请关闭后重试。'; $('detailContent').append(p);
  }
}
$('detailDialog').addEventListener('close', () => { detailRequest?.abort(); detail = null; });
for (const id of ['closeDetail', 'closeDetailBottom']) $(id).addEventListener('click', () => $('detailDialog').close());
$('copyDetail').addEventListener('click', async () => {
  if (!detail) return;
  try { await navigator.clipboard.writeText(JSON.stringify(detail, null, 2)); $('copyStatus').textContent = '已复制'; }
  catch { $('copyStatus').textContent = '复制失败，可从原始 JSON 中手动复制。'; }
});
$('testConnection').addEventListener('click', testConnection);
$('refresh').addEventListener('click', () => load(0));
$('searchForm').addEventListener('submit', event => { event.preventDefault(); load(0); });
$('pageSize').addEventListener('change', () => load(0));
$('currentOnly').addEventListener('change', () => load(0));
$('previous').addEventListener('click', () => load(Math.max(0, offset - Number($('pageSize').value))));
$('next').addEventListener('click', () => load(offset + Number($('pageSize').value)));
$('export').addEventListener('click', () => {
  if (!result?.items.length) return;
  const blob = new Blob([JSON.stringify(result, null, 2)], {type: 'application/json;charset=utf-8'});
  const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url;
  a.download = `opcenter-materials-page-${Math.floor(offset / result.top) + 1}.json`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
(async () => {
  lock(true);
  try {
    const config = await api('/api/opcenter/config');
    $('baseUrl').textContent = config.base_url || '未配置服务地址';
    $('certSource').textContent = config.certificate_source === 'store' ? `Windows / ${config.store_location}` : 'PFX / PKCS#12';
    $('thumbprint').textContent = config.thumbprint || (config.certificate_source === 'pfx' ? '使用 PFX 中的证书' : '未配置，请填写 OPCENTER_X509_THUMBPRINT');
    $('tokenLifetime').textContent = `${config.token_lifetime_seconds} 秒 · 到期前自动重新签发`;
  } catch (error) { showError(error); lock(false); return; }
  lock(false); await testConnection();
})();
