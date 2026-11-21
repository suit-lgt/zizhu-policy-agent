/* Private consultation controller. It deliberately keeps the browser free of raw personal facts. */
'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const terminal = new Set(['completed','clarified','refused','failed','cancelled','interrupted']);
const stateText = {accepted:'已接收',preparing:'检索依据中',validating:'核验回答中',completed:'已完成',clarified:'待补充',refused:'未匹配依据',failed:'生成失败',cancelled:'已停止',interrupted:'处理已中断'};
const modeText = {offline:'离线规则',mock:'测试 Mock',online:'在线证据编排'};
// 展示层：把模型正文里的机器事实锚点 [fact_id] 转成用户可读的上标角标。
// 校验层/存储层仍保留原始锚点（防编造凭证与审计），只在呈现时转换，绝不改原文。
const FACT_ANCHOR_RE = /\[([A-Za-z0-9:_.-]+)\]/g;

// 建立 evidence_id -> {n, doc} 映射：编号按本轮 citations/依据顺序，一一对应。
function anchorIndex(turn) {
  const map = new Map();
  const cites = (turn?.citations?.length ? turn.citations : null) || (turn?.panel?.evidence?.length ? turn.panel.evidence : []);
  cites.forEach((c, i) => {
    const id = c.evidence_id;
    if (id && !map.has(id)) {
      const doc = [c.doc_no, c.article_no].filter(Boolean).join(' ');
      map.set(id, {n: i + 1, doc});
    }
  });
  return map;
}

// 把带锚点的正文渲染成安全 HTML：普通文字全部转义，只有匹配到已知 evidence 的锚点
// 才转成可点击角标；结论/材料/步骤类锚点（c0/m0/p0 等）在展示层隐藏，校验层仍保留。
function renderAnswer(raw, turn) {
  const text = String(raw ?? '');
  const map = anchorIndex(turn);
  let html = '', last = 0, m;
  FACT_ANCHOR_RE.lastIndex = 0;
  while ((m = FACT_ANCHOR_RE.exec(text)) !== null) {
    html += esc(text.slice(last, m.index));
    const hit = map.get(m[1]);
    if (hit) {
      const tip = hit.doc ? ` title="查看政策原文：${esc(hit.doc)}"` : ' title="查看政策原文"';
      html += `<sup class="cite-ref" data-evidence="${esc(m[1])}" role="button" tabindex="0" aria-label="第 ${hit.n} 条政策原文"${tip}>${hit.n}</sup>`;
    }
    last = m.index + m[0].length;
  }
  html += esc(text.slice(last));
  return html;
}

// 服务真实运行模式来自后端 /api/provider，与单条历史轮次的 mode 分开。
// 历史轮次的 mode 只描述“那一轮如何生成”，不能代表当前服务是否已接入在线模型。
const SERVICE_PILL = {
  online:      {text:'在线证据编排', cls:'svc-online',  title:'已接入在线语言服务，回答基于政策原文并经事实核验'},
  mock:        {text:'测试 Mock',    cls:'svc-mock',    title:'联调测试模式，非真实在线服务'},
  offline:     {text:'离线规则',     cls:'svc-offline', title:'本地规则模式，仅作条款摘录与引导'},
  unconfigured:{text:'在线未配置',   cls:'svc-error',   title:'请求在线模式但配置不完整；失败不会被伪装为成功'},
  unknown:     {text:'状态未知',     cls:'svc-error',   title:'无法获取服务状态，请检查网络或稍后刷新'}
};
let serviceMode = null;
const reasonText = {missing_configuration:'在线服务尚未配置完成，请联系管理员',auth_failed:'在线服务鉴权未通过，请联系管理员',rate_limited:'在线服务请求过于频繁，请稍后重试',bad_request:'在线服务拒绝了本次请求，请稍后重试',provider_error:'在线服务暂时不可用，请稍后重试',network_error:'连接在线服务失败或超时，请检查网络',bad_response:'在线服务返回无法解析，请稍后重试',validation_failed:'回答未通过事实核验，已拒绝展示',not_configured_offline:'离线规则模式，仅作条款摘录',execution_failed:'执行出错，可重新尝试',capacity_busy:'当前咨询较多，请稍后重试'};
const taskText = {triaged:'已初判',reviewing:'复核中',approved:'已通过',returned:'已退回',appealed:'申诉中',amended:'已改判'};
const welcome = $('qa').innerHTML, emptyPanel = $('evidence-panel').innerHTML;
let current = null, selected = null, stream = null, epoch = 0, busy = false, switchVersion = false, pending = null;
// 原始提问已落库：新增随 POST /turns 写入，修改走 PUT /turns/{tid}/raw-question；
// 刷新后由 GET /sessions/{sid} 的 turn.raw_question 恢复，页面内存不再保存副本。

function toast(message, kind = 'status') { $('toast').setAttribute('role', kind === 'error' ? 'alert' : 'status'); $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').hidden = true, 4200); }
function setStage(text, busyState = null) {
  const box = $('qa-status'); box.textContent = text; box.hidden = !text;
  if (busyState !== null) $('qa').setAttribute('aria-busy', String(busyState));
}
async function api(path, options = {}) {
  const response = await fetch(path, {credentials:'same-origin', ...options, headers:{'Content-Type':'application/json', ...(options.headers || {})}});
  if (response.status === 401 || response.status === 403) { stopListening(); location.href = 'login.html'; throw new Error('登录已过期或没有访问权限'); }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请稍后再试');
  return data;
}
function stopListening() { if (stream) { stream.close(); stream = null; } epoch++; setBusy(false); }
function setBusy(value) { busy = value; $('ask').disabled = value; $('q').disabled = value; $('cancel-turn').hidden = !value; document.querySelectorAll('[data-question]').forEach(button => { button.disabled = value; }); $('composer-status').textContent = value ? '正在准备依据并核验回答…' : 'Enter 发送 · Shift + Enter 换行'; setStage(value ? '正在检索同版政策依据，随后核验回答。' : '', value); }
function stamp(value) { const d = new Date(value); return isNaN(d) ? '' : d.toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}); }

// 右上角徽章只反映服务真实配置，与历史轮次无关。
function renderServicePill() {
  const pill = $('mode-pill');
  if (!serviceMode) { pill.textContent = '政策咨询'; pill.title = '正在获取服务运行状态…'; return; }
  let key = serviceMode.mode;
  if (key === 'online' && !serviceMode.configured) key = 'unconfigured';
  const conf = SERVICE_PILL[key] || SERVICE_PILL.unknown;
  pill.textContent = conf.text;
  pill.className = 'env-pill ' + conf.cls;
  pill.title = conf.title;
}
async function loadServiceMode() {
  try {
    const data = await api('api/provider');
    serviceMode = {mode: data.mode, configured: !!data.configured, model: data.model};
  } catch (_) { serviceMode = {mode: 'unknown', configured: false}; }
  renderServicePill();
}

async function refreshHistory() {
  const data = await api('api/qa/sessions');
  $('history').innerHTML = data.sessions.length ? data.sessions.map(s => `<button class="history-item ${s.id === current?.id ? 'active' : ''}" data-session="${esc(s.id)}"><b>${esc(s.topic)}</b><time>${esc(stamp(s.updated_at))}</time></button>`).join('') : '<p class="hint" style="padding:12px">还没有咨询记录</p>';
  return data.sessions;
}
async function openSession(id) {
  stopListening(); const token = epoch; pending = null; switchVersion = false;
  let data;
  try { data = await api('api/qa/sessions/' + encodeURIComponent(id)); }
  catch (e) { toast('咨询记录加载失败：' + e.message + '；已保存的原始表述不会丢失，请刷新或重试。', 'error'); return; }
  if (token !== epoch) return;
  current = data; selected = data.turns.at(-1)?.id || null; render(); await refreshHistory();
  const last = data.turns.at(-1); if (last && !terminal.has(last.state)) listen(last.id);
}
async function newSession() { stopListening(); pending = null; switchVersion = false; const data = await api('api/qa/sessions', {method:'POST'}); await openSession(data.id); $('q').focus(); }

function render() {
  $('session-title').textContent = current?.topic || '开始一次政策咨询';
  $('export-all').disabled = !current?.turns?.length; $('delete-session').disabled = !current;
  $('qa').innerHTML = current?.turns?.length ? current.turns.map(turn => {
    const answer = turn.answer || (terminal.has(turn.state) ? '本轮未生成完整回答，你可以重新提问。' : '正在整理可核验的政策依据…');
    const reasonHint = turn.fallback_reason_code && reasonText[turn.fallback_reason_code] ? reasonText[turn.fallback_reason_code] : null;
    const notes = {failed:'本轮生成未完成，可重新尝试；已有初判与复核记录不受影响。',cancelled:'你已停止本轮，后台不会写入完成结果。',interrupted:'服务重启导致本轮中断，可重新尝试。',clarified:'还需要选择项目或本人任务，回答才能更具体。',refused:'当前发布版本未找到可引用依据，本轮不作推断。'};
    const noteText = notes[turn.state] ? (reasonHint && turn.state === 'failed' ? reasonHint + '；' + notes[turn.state] : notes[turn.state]) : (reasonHint && turn.mode === 'offline' ? reasonHint : '');
    const terminalNote = noteText ? `<p class="terminal-note" ${turn.state === 'failed' ? 'role="alert"' : ''}>${esc(noteText)}</p>` : '';
    // 引导类轮次（clarified/refused）没有进入模型生成，mode=offline 只是占位，
    // 不代表服务离线，也不代表这是“离线规则生成的回答”。按状态给出准确标签。
    const guidance = ['clarified','refused'].includes(turn.state);
    const labelMode = guidance ? '信息引导' : (modeText[turn.mode] || '准备中');
    const raw = typeof turn.raw_question === 'string' ? turn.raw_question : null;
    let echoHtml = '';
    if (raw === null) {
      echoHtml = '<div class="raw-echo raw-echo-error" role="alert">原始表述加载失败（服务版本不匹配），请刷新或联系管理员。</div>';
    } else if (raw) {
      echoHtml = `<div class="raw-echo" data-raw-for="${esc(turn.id)}"><b>你输入：</b><span class="raw-echo-text">${esc(raw)}</span><button class="ghost raw-echo-edit" data-edit-raw="${esc(turn.id)}">编辑</button><small>已保存，刷新后自动恢复；整理后的提问见下方</small></div>`;
    } else {
      echoHtml = `<div class="raw-echo raw-echo-empty" data-raw-for="${esc(turn.id)}"><small>原始表述未记录</small><button class="ghost raw-echo-edit" data-edit-raw="${esc(turn.id)}">补充</button></div>`;
    }
    return `<article class="turn ${turn.id === selected ? 'selected' : ''}" data-turn="${esc(turn.id)}"><div class="turn-question">${echoHtml}${esc(turn.question)}</div><div class="turn-answer" data-select="${esc(turn.id)}"><div class="answer-label">政策咨询 <span>${esc(stateText[turn.state] || turn.state)} · ${esc(labelMode)}</span></div>${terminalNote}<div class="answer-text">${renderAnswer(answer, turn)}</div><div class="turn-footer"><button class="${turn.id === selected ? 'chosen' : ''}" data-select="${esc(turn.id)}">${turn.id === selected ? '正在查看依据' : '查看本轮依据'}</button>${terminal.has(turn.state) ? `<button data-export="${esc(turn.id)}">导出本轮</button>` : ''}${['failed','cancelled','interrupted'].includes(turn.state) ? `<button data-retry="${esc(turn.id)}">重新尝试</button>` : ''}${turn.fallback_reason_code && !['failed'].includes(turn.state) && turn.mode === 'offline' ? `<span class="hint">${esc(reasonText[turn.fallback_reason_code] || '离线规则说明')}</span>` : ''}</div></div></article>`;
  }).join('') : welcome;
  const turn = current?.turns?.find(t => t.id === selected); renderServicePill(); renderPanel(turn);
  const version = turn?.context?.policy_version; $('version-notice').hidden = !(version && current.current_version && version !== current.current_version);
  if (!$('version-notice').hidden) $('version-notice').innerHTML = `当前查看历史版本 ${esc(version)}。<button class="ghost" id="use-current">${switchVersion ? '下一问将切换新版' : '下一问使用最新政策'}</button>`;
}
function renderPanel(turn) {
  const p = turn?.panel; if (!p || !Object.keys(p).length) { $('evidence-panel').innerHTML = emptyPanel; return; }
  const label = {supported:'本版依据可核对',needs_verification:'适用信息待核实',no_evidence:'暂缺可引用依据'}[p.scope_status] || '待确认';
  let html = `<div class="scope-card"><span class="scope-status">${esc(label)}</span><b>${esc(p.projects.join('、') || p.title)}</b><small>依据版本 ${esc(p.version || '暂无发布版')}</small></div>`;
  if (p.choices?.length && turn.context.intent !== 'review_progress') html += `<section class="evidence-section"><h3>请选择咨询项目</h3><div class="evidence-actions">${p.choices.map(x => `<button class="ghost" data-project="${esc(x.code)}">${esc(x.name)}</button>`).join('')}</div></section>`;
  if (turn.context.intent === 'review_progress' && !p.task) html += '<section class="evidence-section"><h3>选择本人任务</h3><button class="ghost" id="load-task-options">选择已有初判</button><div id="task-options"></div></section>';
  if (p.task) html += `<section class="evidence-section"><h3>本人任务快照</h3><div class="hint">${esc(stamp(p.task.created_at))} · ${esc(taskText[p.task.status] || p.task.status)}</div>${p.task.items.map(i => `<div class="material-row"><b>${esc(i.project_name)}</b><small>当时初判：${esc(i.conclusion_label)}${i.review_required ? ' · 需人工评审' : ''}</small></div>`).join('')}${p.missing_fields?.length ? `<p class="hint">待核对：${p.missing_fields.map(x => esc(x.name)).join('、')}</p>` : ''}</section>`;
  if (p.materials) html += `<section class="evidence-section"><h3>材料准备</h3>${p.materials.length ? p.materials.map(x => `<div class="material-row"><b>${esc(x.name)}</b><small>${esc(x.source_type)}${x.required ? ' · 原文明确要求' : ''}</small></div>`).join('') : '<p class="hint">暂无可核对清单，请向学校确认。</p>'}<p class="hint">待确认材料不能视为政策必需项。</p></section>`;
  if (p.steps) html += `<section class="evidence-section"><h3>下一步怎么走</h3>${p.steps.map((x, i) => `<div class="step-row"><span class="step-num">${i + 1}</span><div><b>${esc(x.title)}</b><p>${esc(x.description)}</p></div></div>`).join('')}</section>`;
  if (p.evidence?.length) html += `<section class="evidence-section"><h3>政策原文 · ${p.evidence.length} 条</h3>${p.evidence.map((h, i) => `<button class="evidence-ref" data-evidence="${esc(h.evidence_id)}"><b>${i + 1}. ${esc(h.doc_no)} ${esc(h.article_no)}</b><span>${esc(h.quote.slice(0,105))}${h.quote.length > 105 ? '…' : ''}</span><small>查看本版完整原文 ↗</small></button>`).join('')}</section>`;
  html += (p.warnings || []).map(x => `<p class="evidence-warning">${esc(x)}</p>`).join('');
  html += `<div class="evidence-actions"><button class="primary" data-handoff="${esc(turn.id)}">${p.task ? '核对信息，重新初判' : '核对情况，进入初判'} →</button><button class="btn-ghost" data-progress="${esc(turn.id)}" data-task="${esc(p.task?.task_id || '')}">查看本人办理进度 ↗</button></div>`;
  $('evidence-panel').innerHTML = html;
}

async function send(question, extra = {}) {
  if (busy) return; question = (question || $('q').value).trim(); if (question.length < 2) { toast('请至少输入两个字'); return; }
  if (!current) await newSession(); const sid = current.id, token = epoch;
  const body = pending?.sid === sid && pending.question === question ? pending.body : {question,raw_question:question,client_request_id:crypto.randomUUID(),parent_turn_id:current.turns.at(-1)?.id || null,use_current_version:switchVersion,...extra};
  pending = {sid,question,body}; setBusy(true);
  try {
    const data = await api(`api/qa/sessions/${sid}/turns`, {method:'POST',body:JSON.stringify(body)});
    if (token !== epoch) return;
    let session;
    try { session = await api('api/qa/sessions/' + sid); }
    catch (e) {
      if (token === epoch) { setBusy(false); toast('提问已提交，但记录加载失败：' + e.message + '；请刷新后重试，系统不会重复建轮。', 'error'); }
      return;
    }
    if (token !== epoch) return;
    pending = null; switchVersion = false; current = session;
    const saved = current.turns.find(t => t.id === data.turn_id);
    $('q').value = '';
    if (saved && question !== saved.question) toast('提问已按政策语义整理；你的原始文字已保存，可随时修改，刷新后自动恢复。');
    selected = data.turn_id; render(); await refreshHistory(); listen(data.turn_id); $('qa').scrollTop = $('qa').scrollHeight;
  }
  catch (e) { if (token === epoch) { setBusy(false); toast(e.message + '；可原样重试，系统会避免重复创建。', 'error'); } }
}
async function saveRaw(tid, value) {
  const raw = String(value ?? '').trim();
  if (raw.length < 2 || raw.length > 1200) { toast('原始提问须为 2–1200 字', 'error'); return; }
  try {
    const turn = await api(`api/qa/turns/${tid}/raw-question`, {method:'PUT',body:JSON.stringify({raw_question:raw})});
    const i = current?.turns.findIndex(t => t.id === tid) ?? -1;
    if (i >= 0) current.turns[i] = turn;
    render(); toast('原始表述已保存');
  } catch (e) { toast('保存原始表述失败：' + e.message + '；编辑内容仍保留，请重试。', 'error'); }
}
function editRaw(tid) {
  const box = document.querySelector(`[data-raw-for="${tid}"]`);
  if (!box) return;
  const raw = current?.turns.find(t => t.id === tid)?.raw_question || '';
  box.outerHTML = `<div class="raw-echo raw-echo-edit-wrap" data-raw-for="${esc(tid)}"><label class="sr-only" for="raw-${esc(tid)}">原始提问</label><textarea id="raw-${esc(tid)}" rows="2" maxlength="1200">${esc(raw)}</textarea><div class="raw-echo-actions"><button class="primary" data-save-raw="${esc(tid)}">保存</button><button class="ghost" data-cancel-raw="${esc(tid)}">取消</button></div><small>2–1200 字；保存后立即写入咨询记录</small></div>`;
  document.getElementById('raw-' + tid)?.focus();
}
function listen(tid) {
  if (stream) stream.close(); const token = epoch; setBusy(true); stream = new EventSource(`api/qa/turns/${tid}/events`); let answer = '', shown = false;
  stream.addEventListener('meta', event => { if (token !== epoch) return; const data = JSON.parse(event.data), turn = current.turns.find(t => t.id === tid); if (turn) { turn.panel = data.panel; turn.context = data.context; turn.state = 'validating'; if (selected === tid) renderPanel(turn); } setStage('依据已就绪，正在核验回答。', true); });
  stream.addEventListener('chunk', event => { if (token !== epoch) return; answer += JSON.parse(event.data).text; const el = document.querySelector(`[data-turn="${tid}"] .answer-text`); if (el) el.innerHTML = renderAnswer(answer, current.turns.find(t => t.id === tid)); if (!shown) { setStage('回答已通过校验，正在展示。', true); shown = true; } });
  stream.addEventListener('citations', event => { if (token !== epoch) return; const turn = current.turns.find(t => t.id === tid); if (turn) { turn.citations = JSON.parse(event.data); const el = document.querySelector(`[data-turn="${tid}"] .answer-text`); if (el) el.innerHTML = renderAnswer(answer, turn); } });
  stream.addEventListener('done', async () => { if (token !== epoch) return; stream.close(); stream = null; await restore(tid, token); });
  stream.onerror = async () => { if (token !== epoch) return; if (stream) { stream.close(); stream = null; } $('composer-status').textContent = '连接中断，正在查询已保存状态…'; setStage('连接中断，正在查询已保存状态…', true); await restore(tid, token, true); };
}
async function restore(tid, token, broken = false) {
  try { const turn = await api('api/qa/turns/' + tid); if (token !== epoch) return; const index = current.turns.findIndex(t => t.id === tid); if (index >= 0) current.turns[index] = turn; if (!terminal.has(turn.state)) { setBusy(true); render(); $('composer-status').textContent = '连接已恢复，后台仍在处理…'; setStage('连接已恢复，后台仍在处理…', true); setTimeout(() => { if (token === epoch) listen(tid); }, 1500); return; } setBusy(false); render(); setStage('本轮已完成：' + (stateText[turn.state] || turn.state), false); if (broken) toast('已恢复本轮保存状态：' + stateText[turn.state]); }
  catch (e) { if (token === epoch) { setBusy(false); toast('未确认本轮完成状态，请重新打开咨询记录。', 'error'); } }
}
async function openEvidence(eid) { const h = await api(`api/qa/turns/${selected}/evidence/${encodeURIComponent(eid)}`); $('m-title').textContent = h.policy_title; $('m-doc').textContent = `${h.doc_no} ${h.article_no} · 版本 ${h.version_id} · 原文位置 ${h.char_start}–${h.char_end}`; $('m-quote').textContent = h.quote; $('m-flag').textContent = '此处展示所选回答保存的政策原文，不会自动替换为新版。'; $('m-link').hidden = !/^https?:\/\//i.test(h.url || ''); if (!$('m-link').hidden) $('m-link').href = h.url; $('clause-dialog').showModal(); }
function exportRecord(tid) { if (current) location.href = `api/qa/sessions/${current.id}/export?${tid ? 'turn_id=' + encodeURIComponent(tid) : 'scope=all'}`; }
async function handoff(tid) { const data = await api(`api/qa/turns/${tid}/handoff`, {method:'POST',body:'{}'}); location.href = 'triage.html?handoff=' + encodeURIComponent(data.id); }
async function progressHandoff(tid, taskId) {
  if (!taskId) { location.href = 'progress.html'; return; }
  const data = await api(`api/qa/turns/${tid}/progress-handoff`, {method:'POST',body:JSON.stringify({task_id:taskId})});
  location.href = 'progress.html?handoff=' + encodeURIComponent(data.id);
}
async function loadTasks() { const data = await api('api/my-tasks'); $('task-options').innerHTML = data.items.length ? data.items.map(t => `<button class="evidence-ref" data-task="${esc(t.task_id)}"><b>${esc(stamp(t.created_at))} · ${esc(taskText[t.status] || t.status)}</b><small>查看此任务的历史初判</small></button>`).join('') : '<p class="hint">还没有初判任务，请先进入初判。</p>'; }

document.addEventListener('click', async event => { try { const button = event.target.closest('button,a,.cite-ref'); if (!button) return; if (button.dataset.editRaw) { editRaw(button.dataset.editRaw); return; } if (button.dataset.saveRaw) { await saveRaw(button.dataset.saveRaw, document.getElementById('raw-' + button.dataset.saveRaw)?.value); return; } if (button.dataset.cancelRaw) { render(); return; } if (button.classList?.contains('cite-ref')) { await openEvidence(button.dataset.evidence); return; } if (button.dataset.session) await openSession(button.dataset.session); else if (button.dataset.question) { $('q').value = button.dataset.question; $('q').focus(); $('composer-status').textContent = '问题已填入，可修改后发送'; } else if (button.dataset.select) { selected = button.dataset.select; render(); if (matchMedia('(max-width:760px)').matches) $('evidence-fold').open = true; } else if (button.dataset.evidence) await openEvidence(button.dataset.evidence); else if (button.dataset.export) exportRecord(button.dataset.export); else if (button.dataset.handoff) await handoff(button.dataset.handoff); else if (button.dataset.progress) await progressHandoff(button.dataset.progress, button.dataset.task); else if (button.dataset.project) await send('请继续说明申请条件', {confirmed_context:{project_codes:[button.dataset.project]}}); else if (button.dataset.task) await send('解释本人初判结果', {task_id:button.dataset.task}); else if (button.dataset.retry) { const turn = current.turns.find(t => t.id === button.dataset.retry); await send(turn.raw_question || turn.question); } else if (button.id === 'use-current') { switchVersion = true; render(); $('q').focus(); toast('下一问将按当前发布版检索，历史记录保持不变。'); } else if (button.id === 'load-task-options') await loadTasks(); } catch (error) { toast(error.message, 'error'); } });
$('ask-form').addEventListener('submit', event => { event.preventDefault(); send(); });
$('q').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); } });
$('q').addEventListener('input', () => { if (!busy) $('composer-status').textContent = 'Enter 发送 · Shift + Enter 换行'; });
$('new-session').addEventListener('click', () => newSession().catch(error => toast(error.message, 'error')));
$('export-all').addEventListener('click', () => exportRecord());
$('delete-session').addEventListener('click', async () => { if (!current || !confirm('删除这条咨询及其对话记录？已有初判、复核与申诉记录会保留。')) return; try { stopListening(); await api('api/qa/sessions/' + current.id, {method:'DELETE'}); current = null; selected = null; render(); await refreshHistory(); } catch (e) { toast(e.message, 'error'); } });
$('cancel-turn').addEventListener('click', async () => { const tid = current?.turns.at(-1)?.id; if (!tid) return; try { await api('api/qa/turns/' + tid + '/cancel', {method:'POST'}); await restore(tid,epoch); } catch (e) { toast(e.message, 'error'); } });
$('close-clause').addEventListener('click', () => $('clause-dialog').close());
document.addEventListener('keydown', event => {
  if ((event.key === 'Enter' || event.key === ' ') && event.target.classList?.contains('cite-ref')) {
    event.preventDefault(); openEvidence(event.target.dataset.evidence).catch(e => toast(e.message, 'error'));
  }
});
window.addEventListener('pagehide', stopListening);
(async function init() { let prefill = null; try { prefill = sessionStorage.getItem('qa-workbench-prefill'); sessionStorage.removeItem('qa-workbench-prefill'); } catch (_) {} try { try { localStorage.removeItem('zizhu_qa_history'); } catch (_) {} const me = await api('api/auth/me'); if (!me.logged_in || me.role !== 'student') { location.href = 'login.html'; return; } $('me-badge').textContent = `${me.nickname} · 学生`; const logout = document.createElement('a'); logout.href = '#'; logout.textContent = ' 退出'; logout.addEventListener('click', async event => { event.preventDefault(); stopListening(); await fetch('api/auth/logout',{method:'POST'}); location.href = 'login.html'; }); $('me-badge').append(logout); if (matchMedia('(max-width:760px)').matches) { document.querySelector('.history-fold').open = false; $('evidence-fold').open = false; } await api('api/qa/options'); await loadServiceMode(); const history = await refreshHistory(); if (history.length) await openSession(history[0].id); if (prefill) { $('q').value = prefill.slice(0, 1200); $('q').focus(); toast('问题已带入，请检查后手动发送。'); } } catch (e) { toast(e.message, 'error'); } })();
