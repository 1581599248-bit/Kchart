/* mock.js — URL 带 ?mock=1 时拦截 API，生成假数据供无后端自测 */
(function () {
  'use strict';
  if (!/[?&]mock=1/.test(location.search)) return;

  const INDEX_LIST = [
    { ts_code: '000001.SH', name: '上证指数' }, { ts_code: '399001.SZ', name: '深证成指' },
    { ts_code: '399006.SZ', name: '创业板指' }, { ts_code: '000688.SH', name: '科创50' },
    { ts_code: '000300.SH', name: '沪深300' }, { ts_code: '000905.SH', name: '中证500' },
    { ts_code: '000852.SH', name: '中证1000' }, { ts_code: '932000.CSI', name: '中证2000' },
  ];
  const STOCKS = [
    ['600519.SH', '贵州茅台'], ['300750.SZ', '宁德时代'], ['601318.SH', '中国平安'],
    ['000858.SZ', '五粮液'], ['600036.SH', '招商银行'], ['002594.SZ', '比亚迪'],
    ['600900.SH', '长江电力'], ['601899.SH', '紫金矿业'], ['600276.SH', '恒瑞医药'],
    ['300059.SZ', '东方财富'], ['688981.SH', '中芯国际'], ['002415.SZ', '海康威视'],
    ['600030.SH', '中信证券'], ['000333.SZ', '美的集团'], ['601012.SH', '隆基绿能'],
    ['600887.SH', '伊利股份'], ['002230.SZ', '科大讯飞'], ['603259.SH', '药明康德'],
    ['688111.SH', '金山办公'], ['000651.SZ', '格力电器'],
  ];

  function hash(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h; }
  function rng(seed) { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; }

  // 生成模拟 bars：time 为 UNIX 秒
  function genBars(ts_code, timeframe) {
    const r = rng(hash(ts_code + timeframe));
    const bars = [];
    const now = Math.floor(Date.now() / 1000);
    const step = timeframe === '60m' ? 3600 : timeframe === '1d' ? 86400
      : timeframe === '1w' ? 7 * 86400 : 30 * 86400;
    const n = timeframe === '60m' ? 300 : timeframe === '1d' ? 500 : timeframe === '1w' ? 350 : 240;
    let price = 20 + r() * 80;
    let t = now - n * step;
    for (let i = 0; i < n; i++) {
      t += step;
      const drift = (r() - 0.48) * 0.04;
      const o = price;
      const c = price * (1 + drift);
      const h = Math.max(o, c) * (1 + r() * 0.015);
      const l = Math.min(o, c) * (1 - r() * 0.015);
      const v = Math.floor(10000 + r() * 90000 * (1 + Math.abs(drift) * 30));
      bars.push({ time: t, o: +o.toFixed(2), h: +h.toFixed(2), l: +l.toFixed(2), c: +c.toFixed(2), v, amount: v * price * 100 });
      price = c;
    }
    return bars;
  }

  function ma(arr, p, key) {
    return arr.map((_, i) => {
      if (i < p - 1) return null;
      let s = 0; for (let j = i - p + 1; j <= i; j++) s += arr[j][key];
      return +(s / p).toFixed(3);
    });
  }

  function genIndicators(bars) {
    const cls = bars.map(b => ({ close: b.c }));
    const out = { time: bars.map(b => b.time) };
    for (const p of [5, 10, 20, 60]) out['MA' + p] = ma(cls, p, 'close');
    // BOLL
    out.BOLL_MID = ma(cls, 20, 'close');
    out.BOLL_UP = out.BOLL_MID.map((m, i) => m == null ? null : +(m * 1.04).toFixed(3));
    out.BOLL_DN = out.BOLL_MID.map((m, i) => m == null ? null : +(m * 0.96).toFixed(3));
    // MACD 假序列
    const r = rng(7);
    let dif = 0, dea = 0;
    out.DIF = []; out.DEA = []; out.MACD_HIST = [];
    for (let i = 0; i < bars.length; i++) {
      dif = dif * 0.9 + (r() - 0.5) * 0.3; dea = dea * 0.9 + dif * 0.1;
      out.DIF.push(+dif.toFixed(3)); out.DEA.push(+dea.toFixed(3));
      out.MACD_HIST.push(+(2 * (dif - dea)).toFixed(3));
    }
    const mk = (base, amp) => bars.map((_, i) => +(base + Math.sin(i / 9) * amp + (r() - 0.5) * 8).toFixed(2));
    out.K = mk(50, 30); out.D = mk(50, 25); out.J = mk(50, 40);
    out.RSI6 = mk(50, 25); out.RSI12 = mk(50, 20); out.RSI24 = mk(50, 15);
    out.WR6 = mk(-50, 25); out.WR10 = mk(-50, 20);
    out.VOL = bars.map(b => b.v);
    return out;
  }

  function genAnalysis(ts_code, bars) {
    const last = bars[bars.length - 1], c = last.c;
    const mid = bars[Math.floor(bars.length / 2)];
    const annotations = [
      { time: mid.time, price: mid.l * 0.99, kind: 'pattern', label: 'W底确认', direction: 'bull', star: true,
        detail: '双重底颈线突破确认，量度目标≈' + (c * 1.15).toFixed(2),
        lines: [{ t1: bars[Math.floor(bars.length / 3)].time, p1: mid.h, t2: last.time, p2: mid.h, style: 'solid' }] },
      { time: bars[bars.length - 8].time, price: bars[bars.length - 8].h * 1.01, kind: 'divergence', label: '顶背离', direction: 'bear', star: true, detail: '价格新高而 MACD 未新高，动能衰减。' },
      { time: bars[bars.length - 20].time, price: bars[bars.length - 20].l * 0.99, kind: 'fib', label: '0.618 企稳', direction: 'bull', star: false, detail: '回撤至黄金分割 0.618 位企稳。' },
      { time: bars[bars.length - 5].time, price: bars[bars.length - 5].h * 1.01, kind: 'indicator', label: 'RSI 超买', direction: 'bear', star: false, detail: 'RSI6 > 80，短线超买。' },
      { time: bars[bars.length - 30].time, price: c, kind: 'pattern', label: '箱体', direction: 'range', star: false, detail: '箱体震荡区间。',
        zones: [{ t1: bars[bars.length - 60].time, t2: last.time, top: c * 1.05, bottom: c * 0.95, color: 'rgba(240,185,11,0.08)' }] },
    ];
    return {
      annotations,
      summary: {
        trend: '中期上行，短期回调', structure: 'W底已确认，突破颈线后回踩不破',
        momentum: 'MACD 零轴上方死叉，动能衰减', volume: '缩量回调，量价配合健康',
        key_supports: [+(c * 0.95).toFixed(2), +(c * 0.9).toFixed(2)],
        key_resistances: [+(c * 1.06).toFixed(2), +(c * 1.15).toFixed(2)],
        target_price: +(c * 1.15).toFixed(2), stop_loss: +(c * 0.92).toFixed(2),
        risk_reward: 2.4,
        outlook_text: '该标的处于中期上升通道中的回调阶段。W底结构已确认，颈线回踩未破，结构偏多。短期 MACD 动能衰减，RSI 进入超买区，需防范短线震荡。建议关注支撑位表现，跌破止损位则结构失效。',
      },
    };
  }

  const realFetch = window.API; // api.js 已加载
  window.API = Object.assign({}, realFetch, {
    meta: async () => ({
      latest_trade_date: '2026-07-31', db_sha256: 'mock0000', model_version: 'v2-mock',
      index_list: INDEX_LIST,
    }),
    search: async (q) => {
      await sleep(120);
      const all = [
        ...INDEX_LIST.map(x => ({ ts_code: x.ts_code, name: x.name, kind: 'index', market: x.ts_code.endsWith('SH') ? 'SH' : 'SZ' })),
        ...STOCKS.map(([c, n]) => ({ ts_code: c, name: n, kind: 'equity', market: c.endsWith('SH') ? 'SH' : 'SZ' })),
      ];
      return all.filter(x => !q || x.ts_code.includes(q) || x.name.includes(q)).slice(0, 20);
    },
    bars: async (ts_code, timeframe) => {
      await sleep(80);
      const idx = INDEX_LIST.find(x => x.ts_code === ts_code);
      const stk = STOCKS.find(s => s[0] === ts_code);
      return { bars: genBars(ts_code, timeframe || '1d'), name: idx ? idx.name : stk ? stk[1] : ts_code, currency_note: 'mock' };
    },
    indicators: async (ts_code, timeframe) => {
      await sleep(60);
      return genIndicators(genBars(ts_code, timeframe || '1d'));
    },
    analysis: async (ts_code, timeframe) => {
      await sleep(100);
      return genAnalysis(ts_code, genBars(ts_code, timeframe || '1d'));
    },
  });

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  console.log('[mock] 假数据模式已启用');
})();
