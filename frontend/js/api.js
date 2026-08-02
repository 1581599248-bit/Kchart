/* api.js — 统一 fetch 封装 + 错误处理 + “计算中”轮询 */
(function () {
  'use strict';
  const BASE = '/api';

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

  // 后端“计算中”约定：HTTP 202 或 {status:'computing'} → 轮询直到就绪
  async function get(path, params, opts) {
    const maxPoll = (opts && opts.maxPoll) || 60;      // 最多 60 次
    const interval = (opts && opts.interval) || 2000;  // 2s
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

  window.API = {
    toast,
    meta:       () => get('/meta'),
    search:     (q, limit) => get('/search', { q, limit: limit || 20 }),
    bars:       (ts_code, timeframe, start, end) => get('/bars', { ts_code, timeframe, start, end }),
    indicators: (ts_code, timeframe) => get('/indicators', { ts_code, timeframe }),
    analysis:   (ts_code, timeframe, refresh, start) => get('/analysis', { ts_code, timeframe, start, refresh: refresh ? 1 : 0 }),
  };
})();
