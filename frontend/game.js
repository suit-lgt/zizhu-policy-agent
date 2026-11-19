/* 政策闯关：七态状态机（loading／intro／play／reveal／summary／failed／error）。
   前端不保存正确答案：每题结论只来自 /api/game/answer，由后端 triage 现算。 */
(() => {
  'use strict';

  const stage = document.getElementById('stage');
  const hud = document.getElementById('hud');
  const hudIdx = document.getElementById('hud-idx');
  const hudTotal = document.getElementById('hud-total');
  const hudBar = document.getElementById('hud-bar');
  const hudHearts = document.getElementById('hud-hearts');
  const hudScore = document.getElementById('hud-score');
  const mask = document.getElementById('mask');

  const BEST_KEY = 'zizhu_game_best';
  const PER_LEVEL = 150;
  const PERFECT_BONUS = 100;
  const START_TRUST = 3;
  const HEART = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M12 20.4S3.6 15 3.6 9.3A4.6 4.6 0 0 1 12 6.4a4.6 4.6 0 0 1 8.4 2.9c0 5.7-8.4 11.1-8.4 11.1Z" fill="currentColor"/></svg>';
  const MARKS = { eligible: '✓', need_check: '?', not_applicable: '—' };

  const state = {
    view: 'loading',
    total: 0,
    options: [],
    levels: [],
    idx: 0,
    trust: START_TRUST,
    score: 0,
    results: [],
    answering: false,
  };

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  /* ---------- 本地最高分 ---------- */
  function readBest() {
    try {
      const n = parseInt(localStorage.getItem(BEST_KEY) || '0', 10);
      return Number.isFinite(n) && n > 0 ? n : 0;
    } catch (e) { return 0; }
  }
  function writeBest(v) {
    try { localStorage.setItem(BEST_KEY, String(v)); } catch (e) { /* 隐私模式下忽略 */ }
  }

  /* ---------- HUD ---------- */
  function renderHearts() {
    let html = '';
    for (let i = 0; i < START_TRUST; i += 1) {
      html += `<span class="heart${i < state.trust ? '' : ' off'}">${HEART}</span>`;
    }
    hudHearts.innerHTML = html;
    hudHearts.setAttribute('aria-label', `信任值 ${state.trust} 点`);
  }

  function updateHud() {
    const lv = state.levels[state.idx];
    hudIdx.textContent = lv ? lv.index : state.total;
    hudTotal.textContent = `/ ${state.total} 关`;
    const done = state.results.length;
    hudBar.style.width = `${state.total ? (done / state.total) * 100 : 0}%`;
    hudBar.parentElement.setAttribute('aria-valuenow', String(done));
    hudScore.textContent = state.score;
    renderHearts();
  }

  /* ---------- 视图 ---------- */
  function renderIntro() {
    state.view = 'intro';
    hud.classList.add('hidden');
    const best = readBest();
    stage.innerHTML = `
      <div class="game-card">
        <div class="intro-hero">
          <img class="intro-art" src="static/assets/mascot-xiaonuan.svg" alt="小暖，资助政策向导">
          <h1>政策闯关</h1>
          <p>${state.total} 个同学的真实处境，你来判断各属于哪一档。答完就能看到对应的政策原文。</p>
          <ul class="intro-rules">
            <li class="r-eligible"><b>可申请</b><span>条件都对得上，可以准备材料</span></li>
            <li class="r-need_check"><b>需核实</b><span>还缺认定、要等评审，或者有例外情形</span></li>
            <li class="r-not_applicable"><b>暂不适用</b><span>有硬门槛没满足</span></li>
          </ul>
          <p class="intro-trust">3 颗信任值，答错一关熄灭一颗。全部熄灭本轮结束，错 1–2 关还能走完全程。</p>
          ${best ? `<p class="intro-best">历史最高 ${best} 分</p>` : ''}
          <div class="intro-actions">
            <button class="btn-main" type="button" data-action="start">开始闯关</button>
          </div>
        </div>
      </div>`;
  }

  function renderLevel() {
    state.view = 'play';
    state.answering = false;
    hud.classList.remove('hidden');
    const lv = state.levels[state.idx];
    updateHud();
    stage.innerHTML = `
      <article class="game-card">
        <div class="game-kicker">第 ${lv.index} 关 · ${esc(lv.project_name)}</div>
        <h1 class="game-title">${esc(lv.title)}</h1>
        <p class="game-scenario">${esc(lv.scenario)}</p>
        <p class="game-question">${esc(lv.question)}</p>
        <div class="game-hint-row">
          <button class="game-hint" type="button" data-action="hint">看一眼线索</button>
          <p class="game-hint-text hidden" id="hint-text">${esc(lv.hint)}</p>
        </div>
      </article>
      <div class="game-options" role="group" aria-label="选择结论档位">
        ${state.options.map((o, i) => `
          <button class="opt opt-${esc(o.value)}" type="button" data-action="choose" data-choice="${esc(o.value)}">
            <span class="opt-mark" aria-hidden="true">${MARKS[o.value] || i + 1}</span>
            <span class="opt-body"><b>${esc(o.label)}</b><small>${esc(o.desc)}</small></span>
            <span class="opt-key" aria-hidden="true">${i + 1}</span>
          </button>`).join('')}
      </div>`;
  }

  function renderReveal(d, lv, correct) {
    state.view = 'reveal';
    const isLast = state.idx >= state.total - 1;
    const dead = state.trust <= 0;
    const nextLabel = dead && !isLast ? '查看结果' : (isLast ? '看成绩' : '下一关');

    const reasons = (d.reasons || []).map((r) => `
      <button class="game-clause" type="button" data-action="clause" data-id="${esc(r.clause_id)}">
        <span class="gc-doc">${esc(r.doc_no)} ${esc(r.article_no)}</span>
        <span class="gc-quote">${esc(r.quote)}</span>
        <span class="gc-more">点击查看条款全文 ↗</span>
      </button>`).join('');

    const missing = (d.missing_labels || []).length
      ? `<p class="reveal-missing">还缺：${(d.missing_labels || []).map(esc).join('、')}——这就是「需核实」的原因。</p>`
      : '';

    const materials = (d.materials || []).length
      ? `<details class="reveal-more">
           <summary>办理时一般要准备的材料</summary>
           <ul>${d.materials.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>
           <p class="reveal-note">通用清单，以学校资助中心要求为准。</p>
         </details>`
      : '';

    stage.innerHTML = `
      <div class="reveal ${correct ? 'ok' : 'no'}">
        <div class="reveal-inner">
          <div class="reveal-head">
            <span class="reveal-icon" aria-hidden="true">${correct ? '✓' : '!'}</span>
            <div>
              <b>${correct ? '判断正确' : '这里容易踩坑'}</b>
              <small>第 ${lv.index} 关 · ${esc(d.project_name)}</small>
            </div>
          </div>
          <div class="reveal-answer">
            <span class="tier ${esc(d.actual)}">${esc(d.actual_label)}</span>
            <span class="reveal-your">你选的是「${esc(d.chosen_label)}」</span>
          </div>
          <p class="reveal-teach">${esc(d.teach)}</p>
          ${missing}
          <div class="reveal-clauses">
            <div class="rc-title">政策依据（点开可看原文）</div>
            ${reasons || '<p class="reveal-note">本关暂无可用条款，建议咨询学校资助中心。</p>'}
          </div>
          ${materials}
        </div>
      </div>
      <div class="reveal-actions">
        <button class="btn-main" type="button" data-action="next">${nextLabel}</button>
      </div>`;
  }

  function renderSummary() {
    state.view = 'summary';
    hud.classList.add('hidden');

    const right = state.results.filter((r) => r.correct).length;
    const answered = state.results.length;
    const best = readBest();
    const newBest = state.score > best;
    if (newBest) writeBest(state.score);

    const stars = state.score >= 1000 ? 3 : (state.score >= 750 ? 2 : 1);
    const title = stars === 3 ? '政策明白人' : (stars === 2 ? '基本看得懂' : '还要多看看');
    const starHtml = [0, 1, 2].map((i) => `<span class="${i < stars ? '' : 'off'}">★</span>`).join('');

    const tierRows = state.options.map((o) => {
      const list = state.results.filter((r) => r.actual === o.value);
      const ok = list.filter((r) => r.correct).length;
      const cls = list.length && ok === list.length ? 'perfect' : (ok === 0 ? 'zero' : '');
      return `<div class="tier-row">
                <span class="tr-name">${esc(o.label)}</span>
                <span class="tr-val ${cls}">${ok} / ${list.length}</span>
              </div>`;
    }).join('');

    const wrong = state.results.filter((r) => !r.correct);
    const wrongHtml = wrong.length
      ? wrong.map((r) => `
          <div class="wrong-item">
            <div class="wi-title">第 ${r.index} 关 · ${esc(r.title)}</div>
            <div class="wi-line">你选「${esc(r.chosen_label)}」，实际是 <span class="tier ${esc(r.actual)}">${esc(r.actual_label)}</span></div>
          </div>`).join('')
      : '<p class="wrong-empty">全部判断正确，三档口径都拿稳了。</p>';

    stage.innerHTML = `
      <div class="summary">
        <div class="summary-stars" aria-label="获得 ${stars} 星">${starHtml}</div>
        <div class="summary-score"><b>${state.score}</b><span>/ 1000</span></div>
        <div class="summary-title">${title}</div>
        <p class="summary-line">${answered} 关答对 ${right} 关${newBest ? ' · 刷新了你的最高分' : ` · 历史最高 ${Math.max(best, state.score)} 分`}</p>

        <div class="summary-block">
          <h2>三档判断情况</h2>
          ${tierRows}
        </div>

        <div class="summary-block">
          <h2>错题回顾</h2>
          ${wrongHtml}
        </div>

        <div class="summary-actions">
          <button class="btn-main" type="button" data-action="retry">再玩一次</button>
          <a class="ghost" href="triage.html">用 AI 初判看看自己的情况</a>
        </div>
        <p class="summary-disclaimer">AI 初判仅供参考，最终以资助部门认定为准。本游戏不构成任何资格认定。</p>
      </div>`;
  }

  function renderFailed() {
    state.view = 'failed';
    hud.classList.add('hidden');
    const right = state.results.filter((r) => r.correct).length;
    const wrong = state.results.filter((r) => !r.correct);
    const wrongHtml = wrong.length
      ? wrong.map((r) => `
          <div class="wrong-item">
            <div class="wi-title">第 ${r.index} 关 · ${esc(r.title)}</div>
            <div class="wi-line">你选「${esc(r.chosen_label)}」，实际是 <span class="tier ${esc(r.actual)}">${esc(r.actual_label)}</span></div>
          </div>`).join('')
      : '';

    stage.innerHTML = `
      <div class="game-card failed">
        <img class="failed-art" src="static/assets/mascot-xiaonuan.svg" alt="小暖">
        <h1>信任值用完了</h1>
        <p>第 ${state.idx + 1} 关答错后，3 颗信任值全部熄灭。这一轮你答对了 ${right} 关。</p>
        <p class="failed-tip">最容易错的是「需核实」：缺认定、要评审、或有例外时，既不能说能申请，也不能直接说不行。</p>
        ${wrongHtml ? `<div class="summary-block failed-block"><h2>这一轮错在哪</h2>${wrongHtml}</div>` : ''}
        <div class="failed-actions">
          <button class="btn-main" type="button" data-action="retry">再试一次</button>
          <a class="ghost" href="triage.html">先用 AI 初判看看自己的情况</a>
        </div>
      </div>`;
  }

  function renderError(err) {
    state.view = 'error';
    hud.classList.add('hidden');
    stage.innerHTML = `
      <div class="game-card game-error">
        <h2>题库没读出来</h2>
        <p>${esc((err && err.message) || '未知错误')}</p>
        <button class="btn-main" type="button" data-action="reload">重新加载</button>
      </div>`;
  }

  /* ---------- 流程 ---------- */
  function startRun() {
    state.idx = 0;
    state.trust = START_TRUST;
    state.score = 0;
    state.results = [];
    state.answering = false;
    renderLevel();
  }

  function nextStep() {
    if (state.view !== 'reveal') return;
    if (state.trust <= 0) { renderFailed(); return; }
    if (state.idx >= state.total - 1) {
      const allRight = state.results.length === state.total && state.results.every((r) => r.correct);
      if (allRight) state.score += PERFECT_BONUS;
      renderSummary();
      return;
    }
    state.idx += 1;
    renderLevel();
  }

  function showHint(btn) {
    const t = document.getElementById('hint-text');
    if (t) t.classList.remove('hidden');
    btn.disabled = true;
    btn.textContent = '线索已给出';
  }

  async function choose(choice, btn) {
    if (state.answering || state.view !== 'play') return;
    state.answering = true;
    const lv = state.levels[state.idx];
    document.querySelectorAll('.opt').forEach((b) => { b.disabled = true; });
    if (btn) btn.classList.add('is-picked');
    try {
      const res = await fetch('api/game/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ level_id: lv.level_id, choice }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      applyResult(data, lv, choice);
    } catch (err) {
      state.answering = false;
      stage.innerHTML = `
        <div class="game-card game-error">
          <h2>判定没成功</h2>
          <p>${esc((err && err.message) || '网络异常')}</p>
          <button class="btn-main" type="button" data-action="reload">重新读取</button>
        </div>`;
    }
  }

  function applyResult(d, lv, choice) {
    const correct = !!d.correct;
    if (correct) state.score += PER_LEVEL;
    else state.trust -= 1;
    state.results.push({
      level_id: lv.level_id,
      index: lv.index,
      title: lv.title,
      chosen: choice,
      chosen_label: d.chosen_label,
      actual: d.actual,
      actual_label: d.actual_label,
      correct,
    });
    updateHud();
    renderReveal(d, lv, correct);
  }

  /* ---------- 原文弹窗 ---------- */
  async function openClause(id) {
    if (!id) return;
    try {
      const res = await fetch(`api/clause/${encodeURIComponent(id)}`);
      const c = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(c.detail || `HTTP ${res.status}`);
      document.getElementById('m-title').textContent = c.policy_title || '政策原文';
      document.getElementById('m-doc').textContent =
        `${c.doc_no || ''} ${c.article_no || ''} · 原文位置 ${c.char_start}–${c.char_end}`;
      document.getElementById('m-quote').textContent = c.text || '';
      document.getElementById('m-flag').textContent =
        c.status === 'pending_verify' ? '该文件文号与生效日期尚未人工核对，仅供内部验证' : '';
      const link = document.getElementById('m-link');
      if (c.url) { link.href = c.url; link.style.display = ''; } else { link.style.display = 'none'; }
      mask.style.display = 'flex';
    } catch (err) {
      window.alert(`原文读取失败：${(err && err.message) || '未知错误'}`);
    }
  }

  function closeModal() { mask.style.display = 'none'; }

  /* ---------- 事件 ---------- */
  stage.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el || el.disabled) return;
    const action = el.dataset.action;
    if (action === 'start' || action === 'retry') startRun();
    else if (action === 'hint') showHint(el);
    else if (action === 'choose') choose(el.dataset.choice, el);
    else if (action === 'next') nextStep();
    else if (action === 'clause') openClause(el.dataset.id);
    else if (action === 'reload') boot();
  });

  document.getElementById('m-close').addEventListener('click', closeModal);
  mask.addEventListener('click', (e) => { if (e.target === mask) closeModal(); });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeModal(); return; }
    if (state.view === 'intro' && e.key === 'Enter') { e.preventDefault(); startRun(); return; }
    if (state.view === 'reveal' && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); nextStep(); return; }
    if (state.view === 'play') {
      const idx = { '1': 0, '2': 1, '3': 2 }[e.key];
      if (idx === undefined) return;
      const btns = document.querySelectorAll('.opt');
      const b = btns[idx];
      if (b && !b.disabled) { e.preventDefault(); choose(b.dataset.choice, b); }
    }
  });

  /* ---------- 启动 ---------- */
  async function boot() {
    state.view = 'loading';
    try {
      const res = await fetch('api/game/levels', { headers: { Accept: 'application/json' } });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      state.levels = data.levels || [];
      state.options = data.options || [];
      state.total = data.total || state.levels.length;
      if (!state.total || !state.options.length) throw new Error('题库为空，请检查关卡配置');
      renderIntro();
    } catch (err) {
      renderError(err);
    }
  }

  boot();
})();
