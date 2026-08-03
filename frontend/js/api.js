/* api.js — 统一 fetch 封装 + 图表 bundle 浏览器内存缓存 */
(function () {
  'use strict';
  const BASE = '/api';
  const chartCache = new Map();
  const chartPending = new Map();

  function qs(params) {
    const p = Object.entries(params || {})
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v));
    return p.length ? '?' + p.join('&') : '';
  }

  function toast(msg, isErr) {
    let el = document.getElementById('toast');
    if (!el) { el = document.createElement('div'); el.id = 'toast'; document.body.appendChild(el); }
    el.textContent = msg;
    el.className = 'toast' + (isErr ? ' err' : '');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add('hidden'), 3500);
  }

  async function get(path, params, opts) {
    const maxPoll = (opts && opts.maxPoll) || 60;
    const interval = (opts && opts.interval) || 2000;
    for (let i = 0; i <= maxPoll; i++) {
      let resp;
      try {
        resp = await fetch(BASE + path + qs(params));
      } catch (e) {
        throw new Error('网络错误：无法连接后端服务');
      }
      if (resp.status === 202) { await sleep(interval); continue; }
      if (!resp.ok) {
        let detail = '';
        try { detail = (await resp.json()).detail || ''; } catch (_) { /* ignore */ }
        throw new Error(`请求失败 ${resp.status}${detail ? '：' + detail : ''}`);
      }
      const data = await resp.json();
      if (data && data.status === 'computing') { await sleep(interval); continue; }
      return data;
    }
    throw new Error('后端计算超时，请稍后重试');
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function chartKey(tsCode, timeframe) { return `${String(tsCode).toUpperCase()}@${timeframe || '1d'}`; }

  async function chart(tsCode, timeframe, refresh) {
    const key = chartKey(tsCode, timeframe);
    if (!refresh && chartCache.has(key)) return chartCache.get(key);
    if (!refresh && chartPending.has(key)) return chartPending.get(key);
    const p = get('/chart', { ts_code: tsCode, timeframe: timeframe || '1d', refresh: refresh ? 1 : 0 })
      .then(data => { chartCache.set(key, data); return data; })
      .finally(() => chartPending.delete(key));
    chartPending.set(key, p);
    return p;
  }

  function clearChartCache(tsCode, timeframe) {
    chartCache.delete(chartKey(tsCode, timeframe));
  }

  window.API = {
    toast,
    meta:       () => get('/meta'),
    search:     (q, limit) => get('/search', { q, limit: limit || 20 }),
    chart,
    clearChartCache,
    // 保留旧接口，便于回退和 mock 模式。
    bars:       (ts_code, timeframe, start, end) => get('/bars', { ts_code, timeframe, start, end }),
    indicators: (ts_code, timeframe) => get('/indicators', { ts_code, timeframe }),
    analysis:   (ts_code, timeframe, refresh, start) => get('/analysis', { ts_code, timeframe, start, refresh: refresh ? 1 : 0 }),
  };
})();
