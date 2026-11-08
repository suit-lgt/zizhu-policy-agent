/* Private consultation controller. It deliberately keeps the browser free of raw personal facts. */
'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const terminal = new Set(['completed','clarified','refused','failed','cancelled','interrupted']);
const stateText = {accepted:'已接收',preparing:'检索依据中',validating:'核验回答中',completed:'已完成',clarified:'待补充',refused:'未匹配依据',failed:'生成失败',cancelled:'已停止',interrupted:'处理已中断'};
const modeText = {offline:'离线规则',mock:'测试 Mock',online:'在线证据编排'};
const taskText = {triaged:'已初判',reviewing:'复核中',reviewed:'已复核',appealed:'申诉中',closed:'已结束'};
const welcome = $('qa').innerHTML, emptyPanel = $('evidence-panel').innerHTML;
let current = null, selected = null, stream = null, epoch = 0, busy = false, switchVersion = false, pending = null;

function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').hidden = true, 4200); }
async function api(path, options = {}) {
  const response = await fetch(path, {credentials:'same-origin', ...options, headers:{'Content-Type':'application/json', ...(options.headers || {})}});
  if (response.status === 401 || response.status === 403) { stopListening(); location.href = 'login.html'; throw new Error('登录已过期或没有访问权限'); }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请稍后再试');
  return data;
}
function stopListening() { if (stream) { stream.close(); stream = null; } epoch++; setBusy(false); }
function setBusy(value) { busy = value; $('ask').disabled = value; $('q').disabled = value; $('cancel-turn').hidden = !value; document.querySelectorAll('[data-question]').forEach(button => { button.disabled = value; }); $('composer-status').textContent = value ? '正在准备依据并核验回答…' : 'Enter 发送 · Shift + Enter 换行'; }
function stamp(value) { const d = new Date(value); return isNaN(d) ? '' : d.toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}); }

async function refreshHistory() {
  const data = await api('api/qa/sessions');
  $('history').innerHTML = data.sessions.length ? data.sessions.map(s => `<button class="history-item ${s.id === current?.id ? 'active' : ''}" data-session="${esc(s.id)}"><b>${esc(s.topic)}</b><time>${esc(stamp(s.updated_at))}</time></button>`).join('') : '<p class="hint" style="padding:12px">还没有咨询记录</p>';
}
async function openSession(id) {
  stopListening(); const token = epoch; pending = null; switchVersion = false;
  const data = await api('api/qa/sessions/' + encodeURIComponent(id)); if (token !== epoch) return;
  current = data; selected = data.turns.at(-1)?.id || null; render(); await refreshHistory();
  const last = data.turns.at(-1); if (last && !terminal.has(last.state)) listen(last.id);
}
async function newSession() { stopListening(); pending = null; switchVersion = false; const data = await api('api/qa/sessions', {method:'POST'}); await openSession(data.id); $('q').focus(); }

function render() {
  $('session-title').textContent = current?.topic || '开始一次政策咨询';
  $('export-all').disabled = !current?.turns?.length; $('delete-session').disabled = !current;
  $('qa').innerHTML = current?.turns?.length ? current.turns.map(turn => {
    const answer = turn.answer || (terminal.has(turn.state) ? '本轮未生成完整回答，你可以重新提问。' : '正在整理可核验的政策依据…');
    return `<article class="turn ${turn.id === selected ? 'selected' : ''}" data-turn="${esc(turn.id)}"><div class="turn-question">${esc(turn.question)}</div><div class="turn-answer" data-select="${esc(turn.id)}"><div class="answer-label">政策咨询 <span>${esc(stateText[turn.state] || turn.state)} · ${esc(modeText[turn.mode] || '准备中')}</span></div><div class="answer-text">${esc(answer)}</div><div class="turn-footer"><button class="${turn.id === selected ? 'chosen' : ''}" data-select="${esc(turn.id)}">${turn.id === selected ? '正在查看依据' : '查看本轮依据'}</button>${terminal.has(turn.state) ? `<button data-export="${esc(turn.id)}">导出本轮</button>` : ''}${['failed','cancelled','interrupted'].includes(turn.state) ? `<button data-retry="${esc(turn.id)}">重新尝试</button>` : ''}${turn.fallback_reason_code ? '<span class="hint">已使用规则说明</span>' : ''}</div></div></article>`;
  }).join('') : welcome;
  const turn = current?.turns?.find(t => t.id === selected); $('mode-pill').textContent = turn ? (modeText[turn.mode] || '正在准备') : '政策咨询'; renderPanel(turn);
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
  html += `<div class="evidence-actions"><button class="primary" data-handoff="${esc(turn.id)}">${p.task ? '核对信息，重新初判' : '核对情况，进入初判'} →</button><a class="btn-ghost" href="progress.html${p.task ? '?task=' + encodeURIComponent(p.task.task_id) : ''}">查看本人办理进度 ↗</a></div>`;
  $('evidence-panel').innerHTML = html;
}

async function send(question, extra = {}) {
  if (busy) return; question = (question || $('q').value).trim(); if (question.length < 2) { toast('请至少输入两个字'); return; }
  if (!current) await newSession(); const sid = current.id, token = epoch;
  const body = pending?.sid === sid && pending.question === question ? pending.body : {question,client_request_id:crypto.randomUUID(),parent_turn_id:current.turns.at(-1)?.id || null,use_current_version:switchVersion,...extra};
  pending = {sid,question,body}; setBusy(true);
  try { const data = await api(`api/qa/sessions/${sid}/turns`, {method:'POST',body:JSON.stringify(body)}); if (token !== epoch) return; pending = null; switchVersion = false; $('q').value = ''; current = await api('api/qa/sessions/' + sid); if (token !== epoch) return; selected = data.turn_id; render(); await refreshHistory(); listen(data.turn_id); $('qa').scrollTop = $('qa').scrollHeight; }
  catch (e) { if (token === epoch) { setBusy(false); toast(e.message + '；可原样重试，系统会避免重复创建。'); } }
}
function listen(tid) {
  if (stream) stream.close(); const token = epoch; setBusy(true); stream = new EventSource(`api/qa/turns/${tid}/events`); let answer = '';
  stream.addEventListener('meta', event => { if (token !== epoch) return; const data = JSON.parse(event.data), turn = current.turns.find(t => t.id === tid); if (turn) { turn.panel = data.panel; turn.context = data.context; turn.state = 'validating'; if (selected === tid) renderPanel(turn); } });
  stream.addEventListener('chunk', event => { if (token !== epoch) return; answer += JSON.parse(event.data).text; const el = document.querySelector(`[data-turn="${tid}"] .answer-text`); if (el) el.textContent = answer; });
  stream.addEventListener('done', async () => { if (token !== epoch) return; stream.close(); stream = null; await restore(tid, token); });
  stream.onerror = async () => { if (token !== epoch) return; if (stream) { stream.close(); stream = null; } $('composer-status').textContent = '连接中断，正在查询已保存状态…'; await restore(tid, token, true); };
}
async function restore(tid, token, broken = false) {
  try { const turn = await api('api/qa/turns/' + tid); if (token !== epoch) return; const index = current.turns.findIndex(t => t.id === tid); if (index >= 0) current.turns[index] = turn; if (!terminal.has(turn.state)) { setBusy(true); render(); $('composer-status').textContent = '连接已恢复，后台仍在处理…'; setTimeout(() => { if (token === epoch) listen(tid); }, 1500); return; } setBusy(false); render(); if (broken) toast('已恢复本轮保存状态：' + stateText[turn.state]); }
  catch (e) { if (token === epoch) { setBusy(false); toast('未确认本轮完成状态，请重新打开咨询记录。'); } }
}
async function openEvidence(eid) { const h = await api(`api/qa/turns/${selected}/evidence/${encodeURIComponent(eid)}`); $('m-title').textContent = h.policy_title; $('m-doc').textContent = `${h.doc_no} ${h.article_no} · 版本 ${h.version_id} · 原文位置 ${h.char_start}–${h.char_end}`; $('m-quote').textContent = h.quote; $('m-flag').textContent = '此处展示所选回答保存的政策原文，不会自动替换为新版。'; $('m-link').hidden = !/^https?:\/\//i.test(h.url || ''); if (!$('m-link').hidden) $('m-link').href = h.url; $('clause-dialog').showModal(); }
function exportRecord(tid) { if (current) location.href = `api/qa/sessions/${current.id}/export?${tid ? 'turn_id=' + encodeURIComponent(tid) : 'scope=all'}`; }
async function handoff(tid) { const data = await api(`api/qa/turns/${tid}/handoff`, {method:'POST',body:'{}'}); location.href = 'triage.html?handoff=' + encodeURIComponent(data.id); }
async function loadTasks() { const data = await api('api/my-tasks'); $('task-options').innerHTML = data.items.length ? data.items.map(t => `<button class="evidence-ref" data-task="${esc(t.task_id)}"><b>${esc(stamp(t.created_at))} · ${esc(taskText[t.status] || t.status)}</b><small>查看此任务的历史初判</small></button>`).join('') : '<p class="hint">还没有初判任务，请先进入初判。</p>'; }

document.addEventListener('click', async event => { try { const button = event.target.closest('button,a'); if (!button) return; if (button.dataset.session) await openSession(button.dataset.session); else if (button.dataset.question) { $('q').value = button.dataset.question; $('q').focus(); $('composer-status').textContent = '问题已填入，可修改后发送'; } else if (button.dataset.select) { selected = button.dataset.select; render(); if (matchMedia('(max-width:760px)').matches) $('evidence-fold').open = true; } else if (button.dataset.evidence) await openEvidence(button.dataset.evidence); else if (button.dataset.export) exportRecord(button.dataset.export); else if (button.dataset.handoff) await handoff(button.dataset.handoff); else if (button.dataset.project) await send('请继续说明申请条件', {confirmed_context:{project_codes:[button.dataset.project]}}); else if (button.dataset.task) await send('解释本人初判结果', {task_id:button.dataset.task}); else if (button.dataset.retry) { const turn = current.turns.find(t => t.id === button.dataset.retry); await send(turn.question); } else if (button.id === 'use-current') { switchVersion = true; render(); $('q').focus(); toast('下一问将按当前发布版检索，历史记录保持不变。'); } else if (button.id === 'load-task-options') await loadTasks(); } catch (error) { toast(error.message); } });
$('ask-form').addEventListener('submit', event => { event.preventDefault(); send(); });
$('q').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); } });
$('q').addEventListener('input', () => { if (!busy) $('composer-status').textContent = 'Enter 发送 · Shift + Enter 换行'; });
$('new-session').addEventListener('click', () => newSession().catch(error => toast(error.message)));
$('export-all').addEventListener('click', () => exportRecord());
$('delete-session').addEventListener('click', async () => { if (!current || !confirm('删除这条咨询及其对话记录？已有初判、复核与申诉记录会保留。')) return; try { stopListening(); await api('api/qa/sessions/' + current.id, {method:'DELETE'}); current = null; selected = null; render(); await refreshHistory(); } catch (e) { toast(e.message); } });
$('cancel-turn').addEventListener('click', async () => { const tid = current?.turns.at(-1)?.id; if (!tid) return; try { await api('api/qa/turns/' + tid + '/cancel', {method:'POST'}); await restore(tid,epoch); } catch (e) { toast(e.message); } });
$('close-clause').addEventListener('click', () => $('clause-dialog').close());
window.addEventListener('pagehide', stopListening);
(async function init() { let prefill = null; try { prefill = sessionStorage.getItem('qa-workbench-prefill'); sessionStorage.removeItem('qa-workbench-prefill'); } catch (_) {} try { try { localStorage.removeItem('zizhu_qa_history'); } catch (_) {} const me = await api('api/auth/me'); if (!me.logged_in || me.role !== 'student') { location.href = 'login.html'; return; } $('me-badge').textContent = `${me.nickname} · 学生`; const logout = document.createElement('a'); logout.href = '#'; logout.textContent = ' 退出'; logout.addEventListener('click', async event => { event.preventDefault(); stopListening(); await fetch('api/auth/logout',{method:'POST'}); location.href = 'login.html'; }); $('me-badge').append(logout); if (matchMedia('(max-width:760px)').matches) { document.querySelector('.history-fold').open = false; $('evidence-fold').open = false; } await api('api/qa/options'); await refreshHistory(); if (prefill) { $('q').value = prefill.slice(0, 1200); $('q').focus(); toast('问题已带入，请检查后手动发送。'); } } catch (e) { toast(e.message); } })();
