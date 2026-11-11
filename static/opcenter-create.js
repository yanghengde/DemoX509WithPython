'use strict';
const creationStorageKey = 'demo-opcenter-material-creation-v1';
let creationMode = 'edit', creationDraft = null, createdMaterial = null;
function creationFeedback(message, success = false) {
  $('createFeedback').textContent = message;
  $('createFeedback').className = success ? 'create-feedback success' : 'create-feedback';
  $('createFeedback').hidden = !message;
}
function setCreationMode(mode) {
  creationMode = mode;
  const locked = mode !== 'edit';
  $('createForm').querySelectorAll('input,select,textarea').forEach(el => { el.disabled = locked; });
  $('submitCreate').disabled = locked;
  $('submitCreate').hidden = ['created', 'accepted', 'uncertain'].includes(mode);
  $('submitCreate').textContent = mode === 'sending' ? '正在创建…' : '创建物料';
  $('closeCreate').disabled = mode === 'sending';
  $('cancelCreate').disabled = mode === 'sending';
  $('cancelCreate').textContent = mode === 'created' ? '完成' : '取消';
  $('checkCreation').hidden = !['accepted', 'uncertain'].includes(mode);
  $('checkCreation').disabled = false;
  $('viewCreated').hidden = mode !== 'created' || !createdMaterial;
}
function persistCreation() {
  try {
    if (creationDraft) localStorage.setItem(creationStorageKey, JSON.stringify(creationDraft));
    else localStorage.removeItem(creationStorageKey);
  } catch { /* Server-side request identity still prevents replay in this page. */ }
}
function restoreCreation() {
  try {
    const stored = JSON.parse(localStorage.getItem(creationStorageKey) || 'null');
    if (stored?.request_id && typeof stored.NId === 'string') return stored;
  } catch { /* A malformed local draft must not trigger a submission. */ }
  return null;
}
function fillCreation(draft) {
  for (const [key, value] of Object.entries(draft)) {
    const field = $('createForm').elements.namedItem(key);
    if (!field) continue;
    if (field.type === 'checkbox') field.checked = value === true;
    else field.value = value ?? '';
  }
}
async function applyCreationResult(data) {
  if (data.state === 'rejected') {
    creationDraft = null; persistCreation(); setCreationMode('edit'); creationFeedback(data.message); return;
  }
  creationFeedback(`${data.NId} · ${data.Revision}\n${data.message}`, data.ok === true);
  if (data.verified && data.item) {
    createdMaterial = data.item; creationDraft = null; persistCreation(); setCreationMode('created');
    $('keyword').value = data.NId; $('currentOnly').checked = false; await load(0);
  } else {
    setCreationMode(data.ok ? 'accepted' : 'uncertain');
  }
}
async function checkCreation() {
  if (!creationDraft?.request_id) return;
  $('checkCreation').disabled = true;
  try {
    const data = await api(`/api/opcenter/material-creations/${encodeURIComponent(creationDraft.request_id)}`);
    await applyCreationResult(data);
  } catch (error) {
    creationFeedback(error.message || '暂时无法检查创建结果，请稍后重试。');
    if (error.code === 'request_not_found') {
      creationDraft = null; persistCreation(); setCreationMode('edit');
      creationFeedback('服务器没有收到这次创建请求，可核对内容后重新提交。');
    }
  } finally { $('checkCreation').disabled = false; }
}
async function openCreation() {
  $('createForm').reset(); createdMaterial = null; creationFeedback('');
  creationDraft = restoreCreation(); setCreationMode('loading'); $('createDialog').showModal();
  try {
    const options = await api('/api/opcenter/material-creation-options');
    for (const [id, values, placeholder] of [
      ['createUOM', options.units, '请选择计量单位'], ['createClass', options.classes, '使用系统默认类别']]) {
      const select = $(id), first = document.createElement('option'); first.value = ''; first.textContent = placeholder;
      select.replaceChildren(first);
      for (const item of values) {
        const option = document.createElement('option'); option.value = item.NId;
        option.textContent = item.Name && item.Name !== item.NId ? `${item.NId} · ${item.Name}` : item.NId;
        select.append(option);
      }
    }
    if (creationDraft) {
      fillCreation(creationDraft); setCreationMode('uncertain');
      creationFeedback('已恢复上次提交的物料内容，正在检查创建结果…'); await checkCreation();
    } else setCreationMode('edit');
  } catch (error) {
    creationFeedback(error.message || '无法读取创建选项，请关闭表单后重试。');
    setCreationMode(creationDraft ? 'uncertain' : 'loading');
  }
}
$('openCreate').addEventListener('click', openCreation);
for (const id of ['closeCreate', 'cancelCreate']) $(id).addEventListener('click', () => $('createDialog').close());
$('createDialog').addEventListener('cancel', event => { if (creationMode === 'sending') event.preventDefault(); });
$('checkCreation').addEventListener('click', checkCreation);
$('viewCreated').addEventListener('click', () => {
  const item = createdMaterial; $('createDialog').close(); if (item) openDetail(item);
});
$('createForm').addEventListener('submit', async event => {
  event.preventDefault(); if (creationMode !== 'edit' || !$('createForm').reportValidity()) return;
  const fields = new FormData($('createForm'));
  const command = {request_id: crypto.randomUUID()};
  for (const key of ['NId', 'Name', 'Revision', 'UOM', 'Description', 'MaterialClassNId', 'RevisionExpression']) {
    const value = String(fields.get(key) || '').trim(); if (value) command[key] = value;
  }
  command.SerialNumberProfile = $('createSerial').checked;
  command.FirstArticleInspection = $('createInspection').checked;
  for (const key of ['Density', 'MinQuantity', 'MaxRecycleCount']) {
    const value = fields.get(key); if (value !== null && value !== '') command[key] = Number(value);
  }
  if (!command.NId || !command.Name || !command.Revision || !command.UOM) {
    creationFeedback('物料编码、名称、版本和计量单位不能为空。'); return;
  }
  creationDraft = command; persistCreation(); setCreationMode('sending');
  creationFeedback('正在创建物料，请等待提交结果…');
  try {
    const data = await api('/api/opcenter/materials', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(command)});
    await applyCreationResult(data);
  } catch (error) {
    if (error.code === 'creation_uncertain' || error.code === 'request_in_progress' || !error.status || error.status >= 500) {
      setCreationMode('uncertain');
      creationFeedback(`${error.message || '连接中断，未收到创建结果。'}\n请点击“检查创建结果”，当前请求不会自动重发。`);
    } else {
      creationDraft = null; persistCreation(); setCreationMode('edit'); creationFeedback(error.message);
    }
  }
});
