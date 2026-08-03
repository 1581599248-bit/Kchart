/* fast_bundle.js — 用 /api/chart 单请求替换 bars+indicators+analysis 三请求。 */
(function () {
  'use strict';

  function normalizeIndicators(raw) {
    const cols = {};
    if (!raw) return cols;
    const times = Array.isArray(raw.times) ? raw.times : (Array.isArray(raw.time) ? raw.time : null);
    if (!times) return cols;
    for (const key of Object.keys(raw)) {
      if (key === 'times' || key === 'time' || !Array.isArray(raw[key])) continue;
      if (raw[key].length !== times.length) continue;
      cols[key] = raw[key].map((v, i) => v == null ? null : ({ time: times[i], value: v })).filter(Boolean);
    }
    return cols;
  }

  async function legacyBundle(code, timeframe) {
    const [barsResp, indRaw, analysis] = await Promise.all([
      window.API.bars(code, timeframe),
      window.API.indicators(code, timeframe),
      window.API.analysis(code, timeframe, false),
    ]);
    return Object.assign({}, barsResp, { indicators: indRaw, analysis });
  }

  function renderAnalysis(view, data) {
    if (!view) return;
    const slot = view.board.el.querySelector('.analysis-slot');
    try {
      view.data = data || {};
      view.annotations = (view.data.annotations || [])
        .map(a => view._normalize(a))
        .filter(a => a.time != null);
      view._barByTime = new Map(view.board.bars.map(b => [b.time, b]));

      // 单请求路径也必须执行主图质量门控；此前这里绕过了signal_quality.js。
      if (window.SignalQuality && window.SignalQuality.filterAnnotations) {
        view.annotations = window.SignalQuality.filterAnnotations(view, view.annotations);
      }

      view._applyMarkers();
      if (view.showSummary) view._renderSummary(view.data.summary || {});
      if (window.HistoryStructureLabels && window.HistoryStructureLabels.refresh) {
        window.HistoryStructureLabels.refresh(view);
      }
      view._requestRedraw();
    } catch (e) {
      if (view.showSummary) slot.innerHTML = `<div class="analysis-card dim">分析暂不可用：${e.message}</div>`;
    }
  }

  const originalLoad = window.KLineBoard && window.KLineBoard.prototype.load;
  if (!originalLoad) return;

  window.KLineBoard.prototype.load = async function (tsCode) {
    if (tsCode) this.tsCode = tsCode;
    if (!this.tsCode) return;
    const requestId = (this._bundleRequestId || 0) + 1;
    this._bundleRequestId = requestId;
    this.setLoading(true, 'K线与推背图加载中…');
    const slot = this.el.querySelector('.analysis-slot');
    if (this.analysisView && this.analysisView.showSummary) {
      slot.innerHTML = '<div class="analysis-card dim skeleton">推背图分析加载中…</div>';
    }
    try {
      let bundle;
      if (window.API.chart) {
        try {
          bundle = await window.API.chart(this.tsCode, this.timeframe, false);
        } catch (e) {
          if (!String(e.message || '').includes('404')) throw e;
          bundle = await legacyBundle(this.tsCode, this.timeframe);
        }
      } else {
        bundle = await legacyBundle(this.tsCode, this.timeframe);
      }
      if (requestId !== this._bundleRequestId) return;
      this.name = bundle.name || this.tsCode;
      this.el.querySelector('.kl-title .nm').textContent = this.name;
      this.el.querySelector('.kl-title .code').textContent = this.tsCode + ' · 日线';
      this.bars = (bundle.bars || []).map(b => ({
        time: b.time, open: b.o, high: b.h, low: b.l, close: b.c, volume: b.v,
      }));
      this.indCols = normalizeIndicators(bundle.indicators);
      this.candleSeries.setData(this.bars);
      this._renderOverlays();
      for (const name of Object.keys(this.panes)) this._renderPane(name);
      this.mainChart.timeScale().fitContent();
      this._resyncRange();
      this._updateLegend(this.bars[this.bars.length - 1]);
      renderAnalysis(this.analysisView, bundle.analysis);
      this._afterData();

      if (bundle.meta && bundle.meta.refreshing) {
        setTimeout(async () => {
          try {
            window.API.clearChartCache && window.API.clearChartCache(this.tsCode, this.timeframe);
            const fresh = await window.API.chart(this.tsCode, this.timeframe, false);
            if (this.tsCode === fresh.ts_code && fresh.data_asof !== bundle.data_asof) {
              this.load(this.tsCode);
            }
          } catch (_) { /* 保留旧数据即可 */ }
        }, 5000);
      }
    } catch (e) {
      window.API.toast('图表加载失败：' + e.message, true);
      if (this.analysisView && this.analysisView.showSummary) {
        slot.innerHTML = `<div class="analysis-card dim">分析暂不可用：${e.message}</div>`;
      }
    } finally {
      if (requestId === this._bundleRequestId) this.setLoading(false);
    }
  };
})();
