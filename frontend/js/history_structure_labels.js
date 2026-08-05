/* history_structure_labels.js — 历史结构描摹末端显示一次形态名称。
 * 突破颈线/跌破颈线等确认事件仍由主标注层独立显示。
 */
(function () {
  'use strict';

  const AV = window.AnalysisView;
  if (!AV) return;
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
            // 碰撞：首选侧最多 3 层，再试对侧；都不空就取重叠最少的位置，
            // 不丢弃任何结构名称，同时钳在图区内不飞天
            const paneH = scope.bitmapSize.height;
            const mkBox = y => ({
              x1: row.x - w / 2 - 4 * hr,
              x2: row.x + w / 2 + 4 * hr,
              y1: y - 8 * vr,
              y2: y + 8 * vr,
            });
            const hits = b => placed.reduce((n, b2) => n + ((b.x1 < b2.x2 && b.x2 > b2.x1 && b.y1 < b2.y2 && b.y2 > b2.y1) ? 1 : 0), 0);
            let y = null, bestY = row.y + (below ? 31 * vr : -31 * vr), bestHits = Infinity;
            for (let side = 0; side < 2 && y === null; side++) {
              const dir = (side === 0 ? below : !below) ? 1 : -1;
              for (let shift = 0; shift <= 3; shift++) {
                let candY = row.y + dir * (31 + shift * 16) * vr;
                candY = Math.max(10 * vr, Math.min(candY, paneH - 10 * vr));
                const h = hits(mkBox(candY));
                if (h === 0) { y = candY; break; }
                if (h < bestHits) { bestHits = h; bestY = candY; }
              }
            }
            if (y === null) y = bestY;
            const box = mkBox(y);
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

  function refresh(view) {
    install(view);
    if (view.__requestHistoryLabels) view.__requestHistoryLabels();
  }

  window.HistoryStructureLabels = { install, refresh };

  if (!AV.prototype.__historyStructureLabelsInstalled) {
    const previousLoad = AV.prototype.load;
    AV.prototype.load = async function () {
      await previousLoad.call(this);
      refresh(this);
    };
    AV.prototype.__historyStructureLabelsInstalled = true;
  }
})();
