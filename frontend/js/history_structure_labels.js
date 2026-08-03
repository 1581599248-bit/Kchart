/* history_structure_labels.js — 历史结构描摹末端显示一次形态名称。
 * 突破颈线/跌破颈线等确认事件仍由主标注层独立显示。
 */
(function () {
  'use strict';

  const AV = window.AnalysisView;
  if (!AV || AV.prototype.__historyStructureLabelsInstalled) return;
  const GOLD = '#f0b90b';

  function install(view) {
    if (view.__historyStructureLabelPrimitive) return;
    let requestUpdate = null;

    const renderer = {
      draw(target) {
        if (view.quiet || !Array.isArray(view.annotations)) return;
        const labels = view.annotations.filter(a => a.trace_only && a.history_label);
        if (!labels.length) return;

        target.useBitmapCoordinateSpace(scope => {
          const ctx = scope.context;
          const hr = scope.horizontalPixelRatio;
          const vr = scope.verticalPixelRatio;
          const toX = t => view.chart.timeScale().timeToCoordinate(t);
          const toY = p => view.series.priceToCoordinate(p);
          const visible = view.chart.timeScale().getVisibleRange();
          if (!visible) return;

          const rows = [];
          for (const a of labels) {
            if (a.time < visible.from || a.time > visible.to || a.price == null) continue;
            const x = toX(a.time), y = toY(a.price);
            if (x == null || y == null) continue;
            rows.push({ a, x: x * hr, y: y * vr });
          }
          rows.sort((a, b) => a.x - b.x);

          ctx.textAlign = 'center';
          ctx.font = `bold ${10 * hr}px 'Trebuchet MS', sans-serif`;
          const placed = [];
          for (const row of rows) {
            const text = String(row.a.label || '结构');
            const w = ctx.measureText(text).width;
            const below = row.a.direction === 'bull';
            const y = row.y + (below ? 31 * vr : -31 * vr);
            const box = {
              x1: row.x - w / 2 - 4 * hr,
              x2: row.x + w / 2 + 4 * hr,
              y1: y - 8 * vr,
              y2: y + 8 * vr,
            };
            if (placed.some(b => box.x1 < b.x2 && box.x2 > b.x1 && box.y1 < b.y2 && box.y2 > b.y1)) {
              continue;
            }
            placed.push(box);
            ctx.fillStyle = 'rgba(13,17,23,0.78)';
            ctx.fillRect(box.x1, box.y1, box.x2 - box.x1, box.y2 - box.y1);
            ctx.fillStyle = GOLD;
            ctx.textBaseline = 'middle';
            ctx.fillText(text, row.x, y);
          }
        });
      },
    };
    const paneView = { renderer: () => renderer };
    const primitive = {
      attached(params) { requestUpdate = params && params.requestUpdate; },
      paneViews() { return [paneView]; },
    };
    view.series.attachPrimitive(primitive);
    view.__historyStructureLabelPrimitive = primitive;
    view.__requestHistoryLabels = () => { if (requestUpdate) requestUpdate(); };
  }

  const previousLoad = AV.prototype.load;
  AV.prototype.load = async function () {
    await previousLoad.call(this);
    install(this);
    if (this.__requestHistoryLabels) this.__requestHistoryLabels();
  };

  AV.prototype.__historyStructureLabelsInstalled = true;
})();
