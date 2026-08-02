/* app.js — 两分页主控：指数看板 / TOP20 + 单标的全尺寸看板 */
(function () {
  'use strict';
  const $ = s => document.querySelector(s);
  const $$ = s => Array.from(document.querySelectorAll(s));

  const DEFAULT_INDEXES = [
    { ts_code: '000001.SH', name: '上证指数' }, { ts_code: '399001.SZ', name: '深证成指' },
    { ts_code: '399006.SZ', name: '创业板指' }, { ts_code: '000688.SH', name: '科创50' },
    { ts_code: '000300.SH', name: '沪深300' }, { ts_code: '000905.SH', name: '中证500' },
    { ts_code: '000852.SH', name: '中证1000' }, { ts_code: '932000.CSI', name: '中证2000' },
  ];
  const G_LABELS = { G1: '短期反转', G2: '换手量能', G3: '趋势质量', G4: '波动彩票', G5: '结构形态' };

  const state = {
    page: 'board', prevPage: 'board', meta: null,
    indexBoard: null, stockBoard: null, detailBoard: null, currentIndex: null, currentBoardCode: null,
    top20Loaded: false, observer: null,
  };

  // ---------- 分页切换 ----------
  function showPage(name) {
    if (name !== 'detail') state.prevPage = state.page === 'detail' ? state.prevPage : state.page;
    state.page = name;
    $$('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + name));
    $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.page === name));
    if (name === 'search' && !state.top20Loaded) loadTop20(false);
  }

  // ---------- 看板装配 ----------
  function destroyBoard(board) { if (board) { try { board.destroy(); } catch (_) { /* 忽略 */ } } }

  function createFullBoard(container, tsCode) {
    const b = new window.KLineBoard(container, {});
    b.analysisView = new window.AnalysisView(b);
    b.load(tsCode);
    return b;
  }
  function createMiniBoard(container, tsCode) {
    const b = new window.KLineBoard(container, { mini: true });
    b.analysisView = new window.AnalysisView(b, { summary: false, quiet: true });
    b.load(tsCode);
    return b;
  }

  // ---------- 分页1：指数看板 ----------
  async function initMeta() {
    try { state.meta = await window.API.meta(); }
    catch (e) { window.API.toast('元信息加载失败：' + e.message, true); }
    const list = (state.meta && Array.isArray(state.meta.index_list) && state.meta.index_list.length)
      ? state.meta.index_list : DEFAULT_INDEXES;
    const info = $('#meta-info');
    if (state.meta) info.textContent = `最新交易日 ${state.meta.latest_trade_date || '—'} · 模型 ${state.meta.model_version || '—'}`;
    const row = $('#index-btn-row');
    row.innerHTML = '';
    list.forEach((idx, i) => {
      const btn = document.createElement('button');
      btn.className = 'idx-btn';
      btn.textContent = idx.name;
      btn.title = idx.ts_code;
      btn.addEventListener('click', () => selectIndex(idx, btn));
      row.appendChild(btn);
      if (i === 0) selectIndex(idx, btn);
    });
  }

  function selectIndex(idx, btn) {
    $$('.idx-btn').forEach(b => b.classList.toggle('active', b === btn));
    if (state.currentIndex === idx.ts_code) return;
    state.currentIndex = idx.ts_code;
    destroyBoard(state.indexBoard);
    $('#index-board-slot').innerHTML = '';
    state.indexBoard = createFullBoard($('#index-board-slot'), idx.ts_code);
  }

  // ---------- 下半部：个股看板（搜索结果/自选股/历史 装入，不影响上半指数） ----------
  function loadStockBoard(tsCode, name) {
    destroyBoard(state.stockBoard);
    const slot = $('#stock-board-slot');
    slot.innerHTML = '';
    state.stockBoard = createFullBoard(slot, tsCode);
    state.currentBoardCode = tsCode;
    renderSideLists();
    slot.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // ---------- 自选股 / 搜索历史（localStorage 持久化） ----------
  const LS_WATCH = 'ryan_watchlist_v1', LS_HIST = 'ryan_search_history_v1';
  const readLs = k => { try { return JSON.parse(localStorage.getItem(k)) || []; } catch (_) { return []; } };
  const writeLs = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* 忽略 */ } };

  function addHistory(item) {
    const h = readLs(LS_HIST).filter(x => x.ts_code !== item.ts_code);
    h.unshift(item);
    writeLs(LS_HIST, h.slice(0, 15));
  }

  function toggleWatch() {
    const b = state.stockBoard;
    if (!b || !b.tsCode) { window.API.toast('请先搜索并加载一只个股，再加自选', true); return; }
    const code = b.tsCode, name = b.name || code;
    let w = readLs(LS_WATCH);
    if (w.some(x => x.ts_code === code)) {
      w = w.filter(x => x.ts_code !== code);
      window.API.toast(`已移出自选：${name}`);
    } else {
      w.unshift({ ts_code: code, name });
      window.API.toast(`已加入自选：${name}`);
    }
    writeLs(LS_WATCH, w.slice(0, 50));
    renderSideLists();
  }

  function renderSideLists() {
    const cur = state.currentBoardCode;
    const wEl = $('#watch-list'), hEl = $('#history-list');
    if (!wEl || !hEl) return;
    const mkItem = (x, removable) => {
      const div = document.createElement('div');
      div.className = 'sp-item' + (x.ts_code === cur ? ' active' : '');
      div.innerHTML = `<span class="nm">${x.name}</span><span class="code">${x.ts_code}</span>` +
        (removable ? '<span class="rm" title="移出自选">×</span>' : '');
      div.addEventListener('click', e => {
        if (e.target.classList.contains('rm')) {
          writeLs(LS_WATCH, readLs(LS_WATCH).filter(y => y.ts_code !== x.ts_code));
          renderSideLists();
          return;
        }
        loadStockBoard(x.ts_code, x.name);
      });
      return div;
    };
    const wl = readLs(LS_WATCH), h = readLs(LS_HIST);
    wEl.innerHTML = '';
    if (!wl.length) wEl.innerHTML = '<div class="sp-empty">暂无自选股，点击「☆ 加自选」收藏当前标的</div>';
    wl.forEach(x => wEl.appendChild(mkItem(x, true)));
    hEl.innerHTML = '';
    if (!h.length) hEl.innerHTML = '<div class="sp-empty">暂无搜索历史</div>';
    h.forEach(x => hEl.appendChild(mkItem(x, false)));
  }

  // ---------- TOP20 瀑布流 ----------
  async function loadTop20(refresh) {
    const grid = $('#top20-grid');
    const dateEl = $('#top20-date');
    dateEl.textContent = refresh ? '刷新中…' : '计算中…';
    try {
      const data = await window.API.top20(null, refresh);
      const list = (data && Array.isArray(data.items)) ? data.items : [];
      state.top20Loaded = true;
      const dt = (data && data.date) || (state.meta && state.meta.latest_trade_date) || '';
      dateEl.textContent = dt ? `（${dt}）` : '';
      grid.innerHTML = '';
      if (state.observer) state.observer.disconnect();
      state.observer = new IntersectionObserver(onCardVisible, { rootMargin: '300px' });
      (list || []).forEach(item => grid.appendChild(buildTop20Card(item)));
    } catch (e) {
      dateEl.textContent = '';
      window.API.toast('TOP20 加载失败：' + e.message, true);
    }
  }

  function buildTop20Card(item) {
    const card = document.createElement('div');
    card.className = 'top20-card kl-board';
    const pct = item.change_pct;
    const pctCls = pct > 0 ? 'up' : pct < 0 ? 'down' : '';
    const pctCol = pct > 0 ? '#ef5350' : pct < 0 ? '#26a69a' : '#787b86';
    card.innerHTML = `
      <div class="kl-head">
        <span class="rank-badge${item.rank <= 3 ? ' top3' : ''}">${item.rank}</span>
        <span class="kl-title">${item.name}<span class="code">${item.ts_code}</span></span>
        <span style="color:${pctCol};font-size:12px">${pct == null ? '' : (pct > 0 ? '+' : '') + pct + '%'}</span>
        <span style="flex:1"></span>
        <span class="score-badge">${item.score == null ? '—' : item.score}</span>
      </div>
      <div style="padding:8px 12px 0">
        <div class="g-bars">${gBarsHtml(item.group_scores)}</div>
      </div>
      <div class="mini-slot" data-ts="${item.ts_code}"></div>
      <div style="padding:0 12px 10px">
        <div class="brief ${pctCls}">${item.analysis_brief || ''}</div>
        <div class="card-actions"><button class="mini-btn" data-expand>展开完整看板 →</button></div>
      </div>`;
    card.querySelector('[data-expand]').addEventListener('click', () => openDetail(item.ts_code, item.name));
    const slot = card.querySelector('.mini-slot');
    if (state.observer) state.observer.observe(slot);
    return card;
  }

  function gBarsHtml(gs) {
    if (!gs) return '';
    return Object.keys(G_LABELS).map(k => {
      const v = gs[k];
      if (v == null) return '';
      const w = Math.min(100, Math.abs(v) / 3 * 100);
      return `<div class="g-row" title="${G_LABELS[k]}">
        <span class="g-name">${k}</span>
        <span class="g-track"><span class="g-fill${v < 0 ? ' neg' : ''}" style="width:${w}%"></span></span>
        <span class="g-val">${(+v).toFixed(2)}</span></div>`;
    }).join('');
  }

  function onCardVisible(entries) {
    for (const en of entries) {
      if (!en.isIntersecting || en.target.dataset.loaded) continue;
      en.target.dataset.loaded = '1';
      state.observer.unobserve(en.target);
      createMiniBoard(en.target, en.target.dataset.ts);
    }
  }

  // ---------- 分页2：搜索 ----------
  function initSearch() {
    const input = $('#search-input');
    let timer = null;
    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => doSearch(input.value.trim()), 300);
    });
  }
  async function doSearch(q) {
    const tbody = $('#search-tbody');
    const status = $('#search-status');
    if (!q) { tbody.innerHTML = ''; status.textContent = ''; return; }
    status.textContent = '搜索中…';
    try {
      const list = await window.API.search(q, 20);
      status.textContent = `${list.length} 条结果`;
      tbody.innerHTML = '';
      list.forEach(r => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${r.ts_code}</td><td>${r.name}</td><td>${r.market || '—'}</td><td>${r.kind === 'index' ? '指数' : '个股'}</td>`;
        tr.addEventListener('click', () => {
          addHistory({ ts_code: r.ts_code, name: r.name });
          loadStockBoard(r.ts_code, r.name);
        });
        tbody.appendChild(tr);
      });
    } catch (e) {
      status.textContent = '';
      window.API.toast('搜索失败：' + e.message, true);
    }
  }

  // ---------- 单标的全尺寸看板 ----------
  function openDetail(tsCode, name) {
    destroyBoard(state.detailBoard);
    $('#detail-board-slot').innerHTML = '';
    $('#detail-title').textContent = `${name || ''} ${tsCode}`;
    showPage('detail');
    state.detailBoard = createFullBoard($('#detail-board-slot'), tsCode);
  }

  // ---------- 启动 ----------
  function init() {
    $$('.nav-btn').forEach(b => b.addEventListener('click', () => showPage(b.dataset.page)));
    $('#detail-back').addEventListener('click', () => showPage(state.prevPage || 'board'));
    $('#top20-refresh').addEventListener('click', () => loadTop20(true));
    $('#watch-add').addEventListener('click', toggleWatch);
    $('#history-clear').addEventListener('click', () => { writeLs(LS_HIST, []); renderSideLists(); });
    initSearch();
    initMeta();
    renderSideLists();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
