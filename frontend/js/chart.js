/* chart.js — 可复用 K线看板组件（封装 lightweight-charts v4.2.3）
 * 主图 K线 + MA5/10/20/60 + BOLL(勾选)；副图窗格 VOL(默认)/MACD/KDJ/RSI/WR 勾选显隐；
 * 多 chart 时间轴 + 十字光标联动；滚轮缩放/拖拽/双击复位。数据来自 /api/bars 与 /api/indicators。
 */
(function () {
  'use strict';
  const LC = window.LightweightCharts;

  const COLORS = {
    up: '#ef5350', down: '#26a69a', grid: '#2a2e39', text: '#787b86',
    ma5: '#d1d4dc', ma10: '#f0b90b', ma20: '#b275d8', ma60: '#4dc3ff',
    boll: '#8896b3', gold: '#f0b90b',
  };
  const TF_LIST = [
    { key: '1d', label: '日线' },
  ];
  const PANE_DEFS = {
    VOL:  { label: 'VOL' },
    MACD: { label: 'MACD' },
    KDJ:  { label: 'KDJ' },
    RSI:  { label: 'RSI' },
    WR:   { label: 'WR' },
  };
  const LINE_COLORS = { ma5: COLORS.ma5, ma10: COLORS.ma10, ma20: COLORS.ma20, ma60: COLORS.ma60 };

  function baseChartOpts(w, h) {
    return {
      width: w, height: h,
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: COLORS.text, fontSize: 11 },
      grid: { horzLines: { color: COLORS.grid }, vertLines: { color: COLORS.grid } },
      rightPriceScale: { borderColor: COLORS.grid },
      timeScale: { borderColor: COLORS.grid, timeVisible: true, secondsVisible: false },
      crosshair: {
        mode: LC.CrosshairMode.Normal,
        vertLine: { color: '#4a4f63', labelBackgroundColor: '#363c4e' },
        horzLine: { color: '#4a4f63', labelBackgroundColor: '#363c4e' },
      },
      handleScroll: true, handleScale: true,
    };
  }

  // 指标返回形状归一化：支持 {times:[],COL:[...]} / {time:[],COL:[...]} 平行数组
  // 或 {COL:[{time,value}]} 点数组（后端 /api/indicators 实际返回 times 键）
  function normalizeIndicators(raw, times) {
    const cols = {};
    if (!raw) return cols;
    const tarr = Array.isArray(raw.time) ? raw.time : (Array.isArray(raw.times) ? raw.times : null);
    if (tarr) {
      for (const k of Object.keys(raw)) {
        if (k === 'time' || k === 'times' || !Array.isArray(raw[k])) continue;
        if (raw[k].length !== tarr.length) continue;
        cols[k] = raw[k].map((v, i) => (v == null ? null : { time: tarr[i], value: v }))
          .filter(Boolean);
      }
    } else {
      for (const k of Object.keys(raw)) {
        if (!Array.isArray(raw[k])) continue;
        cols[k] = raw[k].map(p => (p && p.value != null ? { time: p.time, value: p.value } : null)).filter(Boolean);
      }
    }
    // 无 time 列时的兜底（不应发生）
    if (!Object.keys(cols).length && times) { /* keep empty */ }
    return cols;
  }

  class KLineBoard {
    /**
     * @param container DOM 容器
     * @param opts { mini?:bool, analysis?:bool, defaultTf?:string, mainHeight?:number }
     */
    constructor(container, opts) {
      opts = opts || {};
      this.opts = opts;
      this.mini = !!opts.mini;
      this.container = container;
      this.tfKeys = TF_LIST.map(t => t.key);
      this.timeframe = opts.defaultTf && this.tfKeys.includes(opts.defaultTf)
        ? opts.defaultTf : (this.tfKeys.includes('1d') ? '1d' : this.tfKeys[0]);
      this.tsCode = null;
      this.name = '';
      this.bars = [];
      this.indCols = {};
      this.charts = [];        // [main, ...panes]
      this.panes = {};         // name -> {el, chart, series:{}}
      this.enabledPanes = new Set(this.mini ? [] : ['VOL']);
      this.bollOn = false;
      this._syncing = false;
      this._xSync = false;
      this._buildDom();
      this._buildCharts();
      this._bindResize();
    }

    _buildDom() {
      const root = document.createElement('div');
      root.className = 'kl-board';
      root.innerHTML = `
        <div class="kl-head">
          <span class="kl-title"><span class="nm">—</span><span class="code"></span></span>
          ${this.tfKeys.length > 1 ? `<div class="tf-group">${TF_LIST.filter(t => this.tfKeys.includes(t.key)).map(t => `<button class="tf-btn${t.key === this.timeframe ? ' active' : ''}" data-tf="${t.key}">${t.label}</button>`).join('')}</div>` : ''}
          ${this.mini ? '' : `<div class="ind-group">
            <label><input type="checkbox" data-boll> BOLL</label>
            ${Object.keys(PANE_DEFS).map(k => `<label><input type="checkbox" data-pane="${k}"${k === 'VOL' ? ' checked' : ''}> ${PANE_DEFS[k].label}</label>`).join('')}
          </div>`}
        </div>
        <div class="kl-body">
          <div class="kl-legend"></div>
          <div class="kl-main"></div>
          <div class="kl-panes"></div>
          <div class="kl-loading skeleton hidden">加载中…</div>
        </div>
        <div class="analysis-slot"></div>`;
      this.container.appendChild(root);
      this.el = root;
      root.klBoard = this;  // 调试/自动化测试挂钩（CDP 验收用）
      this.mainEl = root.querySelector('.kl-main');
      this.panesEl = root.querySelector('.kl-panes');
      this.legendEl = root.querySelector('.kl-legend');
      this.loadingEl = root.querySelector('.kl-loading');
      if (this.mini) this.mainEl.style.height = '220px';

      root.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          if (btn.dataset.tf === this.timeframe) return;
          this.timeframe = btn.dataset.tf;
          root.querySelectorAll('.tf-btn').forEach(b => b.classList.toggle('active', b === btn));
          this.load(this.tsCode);
        });
      });
      const bollCb = root.querySelector('[data-boll]');
      if (bollCb) bollCb.addEventListener('change', () => { this.bollOn = bollCb.checked; this._renderOverlays(); });
      root.querySelectorAll('[data-pane]').forEach(cb => {
        cb.addEventListener('change', () => {
          if (cb.checked) this._addPane(cb.dataset.pane); else this._removePane(cb.dataset.pane);
        });
      });
    }

    _buildCharts() {
      const w = this.mainEl.clientWidth || this.container.clientWidth || 800;
      const h = this.mini ? 220 : (this.mainEl.clientHeight || 380);  // 跟随 .kl-main 容器高度（CSS 控制）
      this.mainChart = LC.createChart(this.mainEl, baseChartOpts(w, h));
      this.charts = [this.mainChart];
      this.candleSeries = this.mainChart.addCandlestickSeries({
        upColor: COLORS.up, downColor: COLORS.down, borderVisible: false,
        wickUpColor: COLORS.up, wickDownColor: COLORS.down,
      });
      this._maSeries = {};
      for (const key of Object.keys(LINE_COLORS)) {
        this._maSeries[key] = this.mainChart.addLineSeries({ color: LINE_COLORS[key], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      }
      this._bollSeries = {
        up: this.mainChart.addLineSeries({ color: COLORS.boll, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false }),
        mid: this.mainChart.addLineSeries({ color: COLORS.boll, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false }),
        dn: this.mainChart.addLineSeries({ color: COLORS.boll, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false }),
      };
      this._wireSync(this.mainChart);
      this._wireLegend();
      this.mainEl.addEventListener('dblclick', () => this.resetView());
      if (this.enabledPanes.has('VOL')) this._addPane('VOL');
    }

    _addPane(name) {
      if (this.panes[name]) return;
      this.enabledPanes.add(name);
      const el = document.createElement('div');
      el.className = 'kl-pane';
      el.dataset.pane = name;
      if (this.mini) el.style.height = '60px';   // 非迷你：高度由 CSS（含手机断点）控制
      this.panesEl.appendChild(el);
      const ph = el.clientHeight || (this.mini ? 60 : 90);
      const chart = LC.createChart(el, baseChartOpts(el.clientWidth || this.mainEl.clientWidth || 800, ph));
      const series = {};
      if (name === 'VOL') {
        series.vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false });
      } else if (name === 'MACD') {
        series.hist = chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
        series.dif = chart.addLineSeries({ color: COLORS.ma10, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        series.dea = chart.addLineSeries({ color: COLORS.ma60, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      } else if (name === 'KDJ') {
        series.k = chart.addLineSeries({ color: COLORS.ma5, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        series.d = chart.addLineSeries({ color: COLORS.ma10, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        series.j = chart.addLineSeries({ color: COLORS.ma20, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      } else if (name === 'RSI') {
        series.r6 = chart.addLineSeries({ color: COLORS.ma5, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        series.r12 = chart.addLineSeries({ color: COLORS.ma10, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      } else if (name === 'WR') {
        series.w6 = chart.addLineSeries({ color: COLORS.ma5, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        series.w10 = chart.addLineSeries({ color: COLORS.ma10, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      }
      this.panes[name] = { el, chart, series };
      this.charts.push(chart);
      this._wireSync(chart);
      el.addEventListener('dblclick', () => this.resetView());
      this._renderPane(name);
      this._resyncRange();
    }

    _removePane(name) {
      const p = this.panes[name];
      if (!p) return;
      this.enabledPanes.delete(name);
      p.chart.remove();
      p.el.remove();
      delete this.panes[name];
      this.charts = this.charts.filter(c => c !== p.chart);
    }

    _wireSync(chart) {
      // 时间轴联动
      chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (this._syncing || !range) return;
        this._syncing = true;
        try { this.charts.forEach(c => { if (c !== chart) c.timeScale().setVisibleLogicalRange(range); }); }
        finally { this._syncing = false; }
      });
      // 十字光标联动
      chart.subscribeCrosshairMove(param => {
        if (this._xSync) return;
        this._xSync = true;
        try {
          this.charts.forEach(c => {
            if (c === chart) return;
            const pane = Object.values(this.panes).find(p => p.chart === c);
            const s = pane ? Object.values(pane.series)[0] : this.candleSeries;
            if (param.time && param.point) {
              const v = s.coordinateToPrice(param.point.y);
              c.setCrosshairPosition(typeof v === 'number' ? v : 0, param.time, s);
            } else c.clearCrosshairPosition();
          });
        } finally { this._xSync = false; }
      });
    }

    _wireLegend() {
      this.mainChart.subscribeCrosshairMove(param => {
        const d = param && param.seriesData ? param.seriesData.get(this.candleSeries) : null;
        this._updateLegend(d || this.bars[this.bars.length - 1]);
      });
    }

    _updateLegend(bar) {
      if (!bar) { this.legendEl.innerHTML = ''; return; }
      const o = bar.open != null ? bar.open : bar.o, h = bar.high != null ? bar.high : bar.h,
        l = bar.low != null ? bar.low : bar.l, c = bar.close != null ? bar.close : bar.c;
      if (c == null) return;
      const pct = o ? ((c - o) / o * 100) : 0;
      const cls = c >= o ? 'up' : 'down';
      const col = c >= o ? COLORS.up : COLORS.down;
      this.legendEl.innerHTML =
        `<b style="color:${col}">开${fmt(o)} 高${fmt(h)} 低${fmt(l)} 收${fmt(c)} <span>${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span></b>`;
    }

    _resyncRange() {
      const r = this.mainChart.timeScale().getVisibleLogicalRange();
      if (r) this.charts.forEach(c => { if (c !== this.mainChart) c.timeScale().setVisibleLogicalRange(r); });
    }

    _bindResize() {
      if (!window.ResizeObserver) return;
      this._ro = new ResizeObserver(() => {
        const w = this.mainEl.clientWidth;
        const h = this.mainEl.clientHeight;
        if (w > 0) this.charts.forEach(c => c.applyOptions({ width: w }));
        if (!this.mini && h > 0) this.mainChart.applyOptions({ height: h });  // 高度也随容器（窗口缩放/CSS 变更时自愈）
        if (!this.mini) {
          // 副图高度同样随容器（手机断点下 .kl-pane 变矮，图表内部高度需同步）
          for (const p of Object.values(this.panes)) {
            const pw = p.el.clientWidth, ph = p.el.clientHeight;
            if (pw > 0 && ph > 0) p.chart.applyOptions({ width: pw, height: ph });
          }
        }
      });
      this._ro.observe(this.mainEl);
    }

    setLoading(on, text) {
      this.loadingEl.textContent = text || '加载中…';
      this.loadingEl.classList.toggle('hidden', !on);
    }

    async load(tsCode) {
      if (tsCode) this.tsCode = tsCode;
      if (!this.tsCode) return;
      this.setLoading(true);
      try {
        const [barsResp, indRaw] = await Promise.all([
          window.API.bars(this.tsCode, this.timeframe),
          window.API.indicators(this.tsCode, this.timeframe).catch(() => null),
        ]);
        this.name = barsResp.name || this.tsCode;
        this.el.querySelector('.kl-title .nm').textContent = this.name;
        this.el.querySelector('.kl-title .code').textContent = this.tsCode + ' · ' + tfLabel(this.timeframe);
        this.bars = (barsResp.bars || []).map(b => ({
          time: b.time, open: b.o, high: b.h, low: b.l, close: b.c, volume: b.v,
        }));
        this.indCols = normalizeIndicators(indRaw, this.bars.map(b => b.time));
        this.candleSeries.setData(this.bars);
        this._renderOverlays();
        for (const name of Object.keys(this.panes)) this._renderPane(name);
        this.mainChart.timeScale().fitContent();
        this._resyncRange();
        this._updateLegend(this.bars[this.bars.length - 1]);
        this._afterData();
      } catch (e) {
        window.API.toast('行情加载失败：' + e.message, true);
      } finally {
        this.setLoading(false);
      }
    }

    _afterData() {
      // 推背图挂载点（由 app.js 按需装配）
      if (this.analysisView) this.analysisView.load();
    }

    _renderOverlays() {
      const cols = this.indCols;
      const set = (series, data) => series.setData(data || []);
      set(this._maSeries.ma5, cols.MA5); set(this._maSeries.ma10, cols.MA10);
      set(this._maSeries.ma20, cols.MA20); set(this._maSeries.ma60, cols.MA60);
      const v = this.bollOn;
      this._bollSeries.up.applyOptions({ visible: v });
      this._bollSeries.mid.applyOptions({ visible: v });
      this._bollSeries.dn.applyOptions({ visible: v });
      if (v) { set(this._bollSeries.up, cols.BOLL_UP); set(this._bollSeries.mid, cols.BOLL_MID); set(this._bollSeries.dn, cols.BOLL_DN); }
    }

    _renderPane(name) {
      const p = this.panes[name];
      if (!p) return;
      const cols = this.indCols;
      const upDown = d => ({ time: d.time, value: d.value, color: d.value >= 0 ? COLORS.up : COLORS.down });
      if (name === 'VOL') {
        const data = this.bars.map(b => ({ time: b.time, value: b.volume || 0, color: b.close >= b.open ? 'rgba(239,83,80,.6)' : 'rgba(38,166,154,.6)' }));
        p.series.vol.setData(data);
      } else if (name === 'MACD') {
        p.series.dif.setData(cols.DIF || []); p.series.dea.setData(cols.DEA || []);
        p.series.hist.setData((cols.MACD_HIST || []).map(upDown));
      } else if (name === 'KDJ') {
        p.series.k.setData(cols.K || []); p.series.d.setData(cols.D || []); p.series.j.setData(cols.J || []);
      } else if (name === 'RSI') {
        p.series.r6.setData(cols.RSI6 || []); p.series.r12.setData(cols.RSI12 || []);
      } else if (name === 'WR') {
        p.series.w6.setData(cols.WR6 || []); p.series.w10.setData(cols.WR10 || []);
      }
    }

    resetView() {
      this.mainChart.timeScale().fitContent();
      this._resyncRange();
    }

    setTitle(t) { this.el.querySelector('.kl-title .nm').textContent = t; }

    destroy() {
      if (this._ro) this._ro.disconnect();
      this.charts.forEach(c => c.remove());
      this.el.remove();
    }
  }

  function tfLabel(tf) { const t = TF_LIST.find(x => x.key === tf); return t ? t.label : tf; }
  function fmt(v) { return v == null ? '--' : (+v).toFixed(2); }

  window.KLineBoard = KLineBoard;
  window.KL_COLORS = COLORS;
})();
