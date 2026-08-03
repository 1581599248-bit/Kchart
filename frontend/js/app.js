/* app.js — 单页主控：指数看板 + 全市场搜索/个股看板（TOP20 已下线） */
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

  const state = {
    page: 'board', meta: null,
    indexBoard: null, stockBoard: null, currentIndex: null, currentBoardCode: null,
  };

  // ---------- 分页切换 ----------
  function showPage(name) {
    state.page = name;
    $$('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + name));
    $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.page === name));
  }

  // ---------- 看板装配 ----------
  function destroyBoard(board) { if (board) { try { board.destroy(); } catch (_) { /* 忽略 */ } } }

  function createFullBoard(container, tsCode) {
    const b = new window.KLineBoard(container, {});
    b.analysisView = new window.AnalysisView(b);
    b.load(tsCode);
    return b;
  }

  // ---------- 指数看板（上半，固定） ----------
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

  // ---------- 全市场搜索 ----------
  function initSearch() {
    const input = $('#search-input');
    let timer = null;
    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => doSearch(input.value.trim()), 250);
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

  // ---------- 启动 ----------
  function init() {
    $$('.nav-btn').forEach(b => b.addEventListener('click', () => showPage(b.dataset.page)));
    $('#watch-add').addEventListener('click', toggleWatch);
    $('#history-clear').addEventListener('click', () => { writeLs(LS_HIST, []); renderSideLists(); });
    initSearch();
    initMeta();
    renderSideLists();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
