/* 政策闯关 v3：状态机（loading／intro／play／reveal／summary／failed／review／error）。
   前端不保存正确答案：每题结论只来自 /api/game/answer，由后端 triage 现算。
   v3 变更：失败页可回看解析原文、review 看完全部关卡、弹窗键盘隔离、线索代价、
   重玩洗牌、失败计分、网络重试本题、HUD 冻结、数值集中、绝对路径 API。 */
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
  const soundToggle = document.getElementById('sound-toggle');

  /* ---------- 音效：Web Audio API 合成，零外部资源 ----------
     默认静音：游戏场景突然出声最突兀，且浏览器要求用户手势后才能播放。
     用户点开关开启 → 首次手势解锁 AudioContext → 按操作语义播放短音。 */
  const SFX_KEY = 'zizhu_game_sfx';
  const Sfx = {
    on: false,
    ctx: null,
    load() {
      try { this.on = localStorage.getItem(SFX_KEY) === '1'; } catch (e) { this.on = false; }
      this.syncBtn();
    },
    save() { try { localStorage.setItem(SFX_KEY, this.on ? '1' : '0'); } catch (e) { /* 隐私模式忽略 */ } },
    syncBtn() {
      if (!soundToggle) return;
      soundToggle.classList.toggle('muted', !this.on);
      soundToggle.setAttribute('aria-pressed', String(this.on));
      soundToggle.setAttribute('aria-label', this.on ? '关闭音效' : '开启音效');
      soundToggle.textContent = this.on ? '♪' : '♪';
    },
    toggle() {
      this.on = !this.on;
      this.save();
      this.syncBtn();
      if (this.on) { this.unlock(); this.play('toggle'); }
    },
    unlock() {
      if (this.ctx) { if (this.ctx.state === 'suspended') this.ctx.resume(); return; }
      try {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (AC) this.ctx = new AC();
      } catch (e) { this.ctx = null; }
    },
    /* tone：单音，freq 频率、dur 时长(s)、type 波形、gain 音量、delay 延迟(s)、slide 终频 */
    tone(freq, dur, type, gain, delay, slide) {
      if (!this.ctx) return;
      const t0 = this.ctx.currentTime + (delay || 0);
      const osc = this.ctx.createOscillator();
      const vol = this.ctx.createGain();
      osc.type = type || 'sine';
      osc.frequency.setValueAtTime(freq, t0);
      if (slide) osc.frequency.exponentialRampToValueAtTime(slide, t0 + dur);
      vol.gain.setValueAtTime(0.0001, t0);
      vol.gain.exponentialRampToValueAtTime(gain, t0 + Math.min(.018, dur / 4));
      vol.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      osc.connect(vol); vol.connect(this.ctx.destination);
      osc.start(t0); osc.stop(t0 + dur + .02);
    },
    /* 各操作对应的音效：音高与时长都做了克制处理，避免与翻牌动画抢节奏 */
    play(kind) {
      if (!this.on) return;
      this.unlock();
      if (!this.ctx) return;
      switch (kind) {
        case 'choose':   // 选档位：轻脆单声确认
          this.tone(520, .07, 'sine', .045); break;
        case 'correct':  // 答对：明亮上行两音（C5→E5）
          this.tone(523, .12, 'sine', .05);
          this.tone(659, .16, 'sine', .05, .09); break;
        case 'wrong':    // 答错：柔和下行两音（G4→E4），不刺耳
          this.tone(392, .13, 'triangle', .045);
          this.tone(330, .18, 'triangle', .04, .1); break;
        case 'next':     // 下一关：短促中性翻页音
          this.tone(440, .06, 'sine', .035); break;
        case 'hint':     // 看线索：轻柔提示
          this.tone(587, .1, 'sine', .03); break;
        case 'toggle':   // 开关开启：一声轻确认
          this.tone(660, .09, 'sine', .04); break;
        default: break;
      }
    },
  };
  Sfx.load();
  if (soundToggle) soundToggle.addEventListener('click', () => Sfx.toggle());

  /* 数值集中：调整难度/计分只改这里，不再散落在各视图模板 */
  const CFG = {
    perLevel: 150,
    perfectBonus: 100,
    startTrust: 3,
    star3: 1000,
    star2: 750,
  };

  const BEST_KEY = 'zizhu_game_best';
  const HEART = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M12 20.4S3.6 15 3.6 9.3A4.6 4.6 0 0 1 12 6.4a4.6 4.6 0 0 1 8.4 2.9c0 5.7-8.4 11.1-8.4 11.1Z" fill="currentColor"/></svg>';
  const MARKS = { eligible: '✓', need_check: '?', not_applicable: '—' };

  const state = {
    view: 'loading',
    total: 0,
    options: [],
    levels: [],
    idx: 0,
    trust: CFG.startTrust,
    score: 0,
    results: [],
    answering: false,
    hintUsed: false,      // 本关是否看过线索（影响全对奖）
    lastAttempt: null,    // { lv, choice } 网络异常重试本题用
    reviewMode: false,
    reviewIdx: 0,
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

  /* ---------- 工具 ---------- */
  function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      const t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  function clauseButtons(reasons) {
    return (reasons || []).map((r) => `
      <button class="game-clause" type="button" data-action="clause" data-id="${esc(r.clause_id)}">
        <span class="gc-doc">${esc(r.doc_no)}${r.article_no ? `<span class="gc-art">${esc(r.article_no)}</span>` : ''}</span>
        <span class="gc-quote">${esc(r.quote)}</span>
        <span class="gc-more">点击查看条款全文 ↗</span>
      </button>`).join('');
  }

  function missingHtml(labels) {
    return (labels || []).length
      ? `<p class="reveal-missing">还缺：${labels.map(esc).join('、')}——这就是「需核实」的原因。</p>` : '';
  }

  function materialsHtml(materials) {
    return (materials || []).length
      ? `<details class="reveal-more">
           <summary>办理时一般要准备的材料</summary>
           <ul>${materials.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>
           <p class="reveal-note">通用清单，以学校资助中心要求为准。</p>
         </details>`
      : '';
  }

  /* 错题项（结算页与失败页共用）：可展开解析 + 政策原文按钮 */
  function wrongItemHtml(r) {
    const d = r.detail || {};
    return `<div class="wrong-item">
      <div class="wi-title">第 ${r.index} 关 · ${esc(r.title)}</div>
      <div class="wi-line">你选「${esc(r.chosen_label)}」，实际是 <span class="tier ${esc(r.actual)}">${esc(r.actual_label)}</span></div>
      <details class="wi-detail"><summary>看解析与政策原文</summary>
        ${d.teach ? `<p class="wi-teach">${esc(d.teach)}</p>` : ''}
        <div class="wi-clauses">${clauseButtons(d.reasons) || '<p class="reveal-note">本关暂无可用条款，建议咨询学校资助中心。</p>'}</div>
      </details>
    </div>`;
  }

  /* ---------- HUD ---------- */
  function renderHearts() {
    let html = '';
    for (let i = 0; i < CFG.startTrust; i += 1) {
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
    hudBar.innerHTML = Array.from({ length: state.total }, (_, i) => {
      const cls = i < done ? 'done' : (i === state.idx && state.view !== 'summary' ? 'current' : '');
      return `<i class="${cls}"></i>`;
    }).join('');
    hudBar.parentElement.setAttribute('aria-valuenow', String(done));
    hudBar.parentElement.setAttribute('aria-valuemax', String(state.total));
    hudScore.textContent = state.score;
    renderHearts();
  }

  /* ---------- 视图 ---------- */
  function renderIntro() {
    state.view = 'intro';
    hud.classList.add('hidden');
    hud.classList.remove('frozen', 'reviewing');
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
          <p class="intro-trust">${CFG.startTrust} 颗信任值，答错一关熄灭一颗。全部熄灭本轮结束，错 1–2 关还能走完全程。</p>
          <p class="intro-hint-note">「看一眼线索」免费，但用过线索的关卡不计入全对奖。</p>
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
    state.hintUsed = false;
    hud.classList.remove('hidden', 'frozen', 'reviewing');
    const lv = state.levels[state.idx];
    updateHud();
    stage.innerHTML = `
      <div class="play-grid">
        <article class="game-card">
          <div class="game-kicker">CASE ${String(lv.index).padStart(2, '0')} · ${esc(lv.project_name)}</div>
          <h1 class="game-title">${esc(lv.title)}</h1>
          <p class="game-scenario">${esc(lv.scenario)}</p>
          <p class="game-question">${esc(lv.question)}</p>
          <div class="game-hint-row">
            <button class="game-hint" type="button" data-action="hint">看一眼线索（用后不计全对奖）</button>
            <p class="game-hint-text hidden" id="hint-text">${esc(lv.hint)}</p>
          </div>
        </article>
        <div class="play-aside">
          <div class="play-aside-head">判读结论<small>这属于哪一档？</small></div>
          <div class="play-options" role="group" aria-label="选择结论档位">
            ${state.options.map((o, i) => `
              <button class="opt opt-${esc(o.value)}" type="button" data-action="choose" data-choice="${esc(o.value)}">
                <span class="opt-mark" aria-hidden="true">${MARKS[o.value] || i + 1}</span>
                <span class="opt-body"><b>${esc(o.label)}</b><small>${esc(o.desc)}</small></span>
                <span class="opt-key" aria-hidden="true">${i + 1}</span>
              </button>`).join('')}
          </div>
        </div>
      </div>`;
  }

  function renderReveal(d, lv, correct) {
    state.view = 'reveal';
    const isLast = state.idx >= state.total - 1;
    const dead = state.trust <= 0;
    const nextLabel = dead && !isLast ? '查看结果' : (isLast ? '看成绩' : '下一关');
    const trap = lv.trap || '三档口径';

    stage.innerHTML = `
      <div class="reveal-grid">
        <div class="reveal ${correct ? 'ok' : 'no'}">
          <div class="reveal-ribbon">${correct ? '判读正确 · CASE CLOSED' : '这里容易踩坑 · RECHECK'}</div>
          <div class="reveal-inner">
            <div class="reveal-head">
              <span class="reveal-icon" aria-hidden="true">${MARKS[d.actual] || '?'}</span>
              <div>
                <b>${correct ? '判断正确' : '这题判错了'}</b>
                <small>第 ${lv.index} 关 · ${esc(d.project_name)}</small>
              </div>
            </div>
            <div class="reveal-answer">
              <div class="answer-box wrong"><span class="ab-label">你选的</span><span class="ab-value">${esc(d.chosen_label)}</span></div>
              <span class="answer-arrow" aria-hidden="true">→</span>
              <div class="answer-box right"><span class="ab-label">正确档位</span><span class="ab-value"><span class="tier ${esc(d.actual)}">${esc(d.actual_label)}</span></span></div>
            </div>
            ${state.hintUsed ? '<p class="hint-penalty">本关看过线索，不计入全对奖。</p>' : ''}
            <p class="reveal-teach">${esc(d.teach)}</p>
            ${missingHtml(d.missing_labels)}
            <div class="trap-tag">⚑ 本关考点：${esc(trap)}</div>
          </div>
        </div>
        <aside class="reveal-evidence">
          <div class="rc-title">政策依据 · 点开核对原文</div>
          ${clauseButtons(d.reasons) || '<p class="reveal-note">本关暂无可用条款，建议咨询学校资助中心。</p>'}
          ${materialsHtml(d.materials)}
        </aside>
      </div>
      <div class="reveal-actions">
        <button class="btn-main" type="button" data-action="next">${nextLabel}</button>
      </div>`;
  }

  function renderSummary() {
    state.view = 'summary';
    hud.classList.remove('hidden');
    hud.classList.add('frozen');
    hud.classList.remove('reviewing');
    updateHud();

    const right = state.results.filter((r) => r.correct).length;
    const answered = state.results.length;
    const best = readBest();
    const newBest = state.score > best;
    if (newBest) writeBest(state.score);

    const stars = state.score >= CFG.star3 ? 3 : (state.score >= CFG.star2 ? 2 : 1);
    const title = stars === 3 ? '政策明白人' : (stars === 2 ? '基本看得懂' : '还要多看看');
    const starHtml = [0, 1, 2].map((i) => `<span class="${i < stars ? '' : 'off'}">★</span>`).join('');

    const tierRows = state.options.map((o) => {
      const list = state.results.filter((r) => r.actual === o.value);
      const ok = list.filter((r) => r.correct).length;
      const cls = list.length && ok === list.length ? 'perfect' : (ok === 0 ? 'zero' : '');
      const pct = list.length ? Math.round((ok / list.length) * 100) : 0;
      return `<div class="tier-row">
                <span class="tr-name">${esc(o.label)}</span>
                <span class="tr-bar"><i style="width:${pct}%"></i></span>
                <span class="tr-val ${cls}">${ok} / ${list.length}</span>
              </div>`;
    }).join('');

    const wrong = state.results.filter((r) => !r.correct);
    const wrongHtml = wrong.length
      ? wrong.map(wrongItemHtml).join('')
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
          <h2>错题回顾 · 点开看解析与原文</h2>
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
    hud.classList.remove('hidden');
    hud.classList.add('frozen');
    hud.classList.remove('reviewing');
    updateHud();

    const right = state.results.filter((r) => r.correct).length;
    const wrong = state.results.filter((r) => !r.correct);
    const best = readBest();
    if (state.score > best) writeBest(state.score);
    const wrongHtml = wrong.length ? wrong.map(wrongItemHtml).join('') : '';
    const canReview = state.idx < state.total - 1;

    stage.innerHTML = `
      <div class="game-card failed">
        <img class="failed-art" src="static/assets/mascot-xiaonuan.svg" alt="小暖">
        <h1>信任值用完了</h1>
        <p>第 ${state.idx + 1} 关答错后，${CFG.startTrust} 颗信任值全部熄灭。这一轮你答对了 ${right} 关，得分 ${state.score} 分。</p>
        <p class="failed-best">本轮 ${state.score} 分 · 历史最高 ${Math.max(best, state.score)} 分</p>
        <p class="failed-tip">最容易错的是「需核实」：缺认定、要评审、或有例外时，既不能说能申请，也不能直接说不行。</p>
        ${wrongHtml ? `<div class="summary-block failed-block"><h2>这一轮错在哪 · 点开看解析与原文</h2>${wrongHtml}</div>` : ''}
        <div class="failed-actions">
          ${canReview ? `<button class="btn-main" type="button" data-action="review">看完全部 ${state.total} 关（不计分）</button>` : ''}
          <button class="${canReview ? 'btn-ghost' : 'btn-main'}" type="button" data-action="retry">再试一次</button>
          <a class="ghost" href="triage.html">先用 AI 初判看看自己的情况</a>
        </div>
      </div>`;
  }

  /* ---------- Review：失败后看完全部关卡（不计分、不扣信任值） ---------- */
  function startReview() {
    state.view = 'review';
    state.reviewMode = true;
    state.reviewIdx = 0;
    hud.classList.remove('hidden', 'frozen');
    hud.classList.add('reviewing');
    hudScore.textContent = '复习';
    hudHearts.innerHTML = '';
    renderReviewStep();
  }

  async function renderReviewStep() {
    if (state.reviewIdx >= state.total) { renderReviewDone(); return; }
    const lv = state.levels[state.reviewIdx];
    hudIdx.textContent = lv.index;
    hudTotal.textContent = `/ ${state.total} 关 · 复习`;
    hudBar.innerHTML = Array.from({ length: state.total }, (_, i) => {
      const cls = i < state.reviewIdx ? 'done' : (i === state.reviewIdx ? 'current' : '');
      return `<i class="${cls}"></i>`;
    }).join('');
    hudBar.parentElement.setAttribute('aria-valuenow', String(state.reviewIdx));
    stage.innerHTML = '<div class="game-card game-loading"><div class="loading-dot"></div><p>正在调取参考答案…</p></div>';
    try {
      const d = await postAnswer(lv.level_id, state.options[0].value);
      renderReviewReveal(d, lv);
    } catch (err) {
      stage.innerHTML = `<div class="game-card game-error"><h2>参考答案没读出来</h2><p>${esc((err && err.message) || '网络异常')}</p><button class="btn-main" type="button" data-action="review-retry">重试</button></div>`;
    }
  }

  function renderReviewReveal(d, lv) {
    const isLast = state.reviewIdx >= state.total - 1;
    stage.innerHTML = `
      <div class="reveal-grid">
        <div class="reveal review">
          <div class="reveal-ribbon">参考答案 · CASE ${String(lv.index).padStart(2, '0')}</div>
          <div class="reveal-inner">
            <div class="reveal-head">
              <span class="reveal-icon" aria-hidden="true">${MARKS[d.actual] || '?'}</span>
              <div><b>这一关的正确档位</b><small>第 ${lv.index} 关 · ${esc(d.project_name)}</small></div>
            </div>
            <div class="review-answer"><span class="tier ${esc(d.actual)}">${esc(d.actual_label)}</span></div>
            <p class="reveal-teach">${esc(d.teach)}</p>
            ${missingHtml(d.missing_labels)}
            ${lv.trap ? `<div class="trap-tag">⚑ 本关考点：${esc(lv.trap)}</div>` : ''}
          </div>
        </div>
        <aside class="reveal-evidence">
          <div class="rc-title">政策依据 · 点开核对原文</div>
          ${clauseButtons(d.reasons) || '<p class="reveal-note">本关暂无可用条款，建议咨询学校资助中心。</p>'}
          ${materialsHtml(d.materials)}
        </aside>
      </div>
      <div class="reveal-actions">
        <button class="btn-main" type="button" data-action="${isLast ? 'review-done' : 'review-next'}">${isLast ? '复习完成' : '下一关'}</button>
      </div>`;
  }

  function renderReviewDone() {
    state.view = 'review-done';
    hud.classList.add('frozen');
    stage.innerHTML = `
      <div class="summary review-done">
        <div class="review-done-mark">✓</div>
        <div class="summary-title">全部 ${state.total} 关已看完</div>
        <p class="summary-line">复习模式不计分。回到闯关，用刚学的口径再判一次。</p>
        <div class="summary-actions"><button class="btn-main" type="button" data-action="retry">再玩一次</button></div>
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
    state.levels = shuffle(state.levels);
    state.levels.forEach((lv, i) => { lv.index = i + 1; });
    state.idx = 0;
    state.trust = CFG.startTrust;
    state.score = 0;
    state.results = [];
    state.answering = false;
    state.hintUsed = false;
    state.lastAttempt = null;
    state.reviewMode = false;
    state.reviewIdx = 0;
    hud.classList.remove('frozen', 'reviewing');
    renderLevel();
  }

  function nextStep() {
    if (state.view !== 'reveal') return;
    Sfx.play('next');
    if (state.trust <= 0) { renderFailed(); return; }
    if (state.idx >= state.total - 1) {
      const allRight = state.results.length === state.total
        && state.results.every((r) => r.correct)
        && state.results.every((r) => !r.hintUsed);
      if (allRight) state.score += CFG.perfectBonus;
      renderSummary();
      return;
    }
    state.idx += 1;
    renderLevel();
  }

  function showHint(btn) {
    const t = document.getElementById('hint-text');
    if (t) t.classList.remove('hidden');
    state.hintUsed = true;
    btn.disabled = true;
    btn.textContent = '线索已给出';
    Sfx.play('hint');
  }

  async function postAnswer(levelId, choice) {
    const res = await fetch('/api/game/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level_id: levelId, choice }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
  }

  async function choose(choice, btn) {
    if (state.answering || state.view !== 'play') return;
    state.answering = true;
    Sfx.play('choose');
    const lv = state.levels[state.idx];
    state.lastAttempt = { lv, choice };
    document.querySelectorAll('.opt').forEach((b) => { b.disabled = true; });
    if (btn) btn.classList.add('is-picked');
    try {
      const data = await postAnswer(lv.level_id, choice);
      applyResult(data, lv, choice);
    } catch (err) {
      state.answering = false;
      stage.innerHTML = `
        <div class="game-card game-error">
          <h2>判定没成功</h2>
          <p>${esc((err && err.message) || '网络异常')}</p>
          <div class="game-error-actions">
            <button class="btn-main" type="button" data-action="retry-answer">重试本题</button>
            <button class="btn-ghost" type="button" data-action="reload">重新加载</button>
          </div>
        </div>`;
    }
  }

  function retryAnswer() {
    if (!state.lastAttempt || state.view !== 'play') { boot(); return; }
    const { choice } = state.lastAttempt;
    renderLevel();
    choose(choice);
  }

  function applyResult(d, lv, choice) {
    const correct = !!d.correct;
    Sfx.play(correct ? 'correct' : 'wrong');
    if (correct) state.score += CFG.perLevel;
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
      hintUsed: state.hintUsed,
      detail: d,
    });
    updateHud();
    renderReveal(d, lv, correct);
  }

  /* ---------- 原文弹窗 ---------- */
  async function openClause(id) {
    if (!id) return;
    try {
      const res = await fetch(`/api/clause/${encodeURIComponent(id)}`);
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
    else if (action === 'retry-answer') retryAnswer();
    else if (action === 'review') startReview();
    else if (action === 'review-next') { Sfx.play('next'); state.reviewIdx += 1; renderReviewStep(); }
    else if (action === 'review-done') renderReviewDone();
    else if (action === 'review-retry') renderReviewStep();
  });

  document.getElementById('m-close').addEventListener('click', closeModal);
  mask.addEventListener('click', (e) => { if (e.target === mask) closeModal(); });

  document.addEventListener('keydown', (e) => {
    /* 弹窗打开时：只响应 Escape 关闭，其余按键一律不透传给游戏流程 */
    if (mask.style.display === 'flex') {
      if (e.key === 'Escape') { e.preventDefault(); closeModal(); }
      return;
    }
    if (e.key === 'Escape') { closeModal(); return; }
    if (state.view === 'intro' && e.key === 'Enter') { e.preventDefault(); startRun(); return; }
    if (state.view === 'reveal' && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); nextStep(); return; }
    if (state.view === 'review' && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      if (state.reviewIdx >= state.total - 1) renderReviewDone();
      else { Sfx.play('next'); state.reviewIdx += 1; renderReviewStep(); }
      return;
    }
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
      const res = await fetch('/api/game/levels', { headers: { Accept: 'application/json' } });
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
