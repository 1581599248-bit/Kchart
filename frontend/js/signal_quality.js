/* signal_quality.js — 主图信号质量门控。
 *
 * 后端保留完整候选用于研究；前端只展示可交易性更高的事件：
 * 1) 未突破的形态构筑不直接上图；
 * 2) 同方向、相近时间的重复信号只留优先级最高者；
 * 3) 同一标签设置冷却期；
 * 4) Fib保持中性位置标签，不与方向信号竞争。
 */
(function () {
  'use strict';

  const AV = window.AnalysisView;
  if (!AV || AV.prototype.__qualityGateInstalled) return;

  const CONFIRMED_PATTERN_LABELS = new Set([
    '突破颈线', '跌破颈线', '向上突破', '向下跌破',
    '突破趋势', '跌破趋势', '结构失效',
  ]);

  const KIND_PRIORITY = {
    harmonic: 100,
    pattern: 90,
    divergence: 84,
    trend: 76,
    indicator: 70,
    fibonacci: 55,
  };

  function labelPriority(a) {
    let p = KIND_PRIORITY[a.kind] || 40;
    if (a.star) p += 30;
    if (a.label === 'MACD顶背离' || a.label === 'MACD底背离') p += 6;
    if (a.label === 'Fib0.618') p += 5;
    if (a.label === 'Fib0.5') p += 3;
    if (a.label === '结构失效') p -= 8;
    return p;
  }

  function shouldKeepBasic(a, lastBarIdx, idx) {
    if (a.kind === 'pattern') {
      if (a.star || CONFIRMED_PATTERN_LABELS.has(a.label)) return true;
      // 形态构筑阶段容易反复重绘，只保留在分析卡，不直接污染主图。
      return false;
    }
    if (a.active === false && idx < lastBarIdx - 160) return false;
    return true;
  }

  function filterAnnotations(view, annotations) {
    const bars = view.board && Array.isArray(view.board.bars) ? view.board.bars : [];
    if (!bars.length || !annotations.length) return annotations;

    const indexByTime = new Map(bars.map((b, i) => [b.time, i]));
    const lastBarIdx = bars.length - 1;
    const rows = annotations
      .map(a => ({ a, idx: indexByTime.has(a.time) ? indexByTime.get(a.time) : -1 }))
      .filter(x => x.idx >= 0 && shouldKeepBasic(x.a, lastBarIdx, x.idx))
      .sort((x, y) => x.idx - y.idx || labelPriority(y.a) - labelPriority(x.a));

    const kept = [];
    const lastLabel = new Map();

    for (const row of rows) {
      const a = row.a;
      const idx = row.idx;
      const prio = labelPriority(a);
      const label = String(a.label || '');

      // 同一标签30根以内只保留更重要的一次；结构确认与谐波星标例外。
      const previous = lastLabel.get(label);
      if (previous && idx - previous.idx < 30 && !a.star) {
        if (prio <= previous.prio) continue;
        const pos = kept.indexOf(previous.row);
        if (pos >= 0) kept.splice(pos, 1);
      }

      // Fib属于中性位置提示：同一根最多保留一个，以0.618/0.5优先。
      if (a.kind === 'fibonacci') {
        const conflict = kept.filter(k => k.a.kind === 'fibonacci' && Math.abs(k.idx - idx) <= 2);
        if (conflict.length) {
          const best = conflict.reduce((m, k) => labelPriority(k.a) > labelPriority(m.a) ? k : m);
          if (labelPriority(best.a) >= prio) continue;
          conflict.forEach(k => {
            const pos = kept.indexOf(k);
            if (pos >= 0) kept.splice(pos, 1);
          });
        }
        const item = { a, idx, prio };
        kept.push(item);
        lastLabel.set(label, { idx, prio, row: item });
        continue;
      }

      // 同方向8根内属于同一交易叙事，只留最有解释力的一项。
      const directional = kept.filter(k =>
        k.a.kind !== 'fibonacci'
        && k.a.direction === a.direction
        && Math.abs(k.idx - idx) <= 8
      );
      if (directional.length && !a.star) {
        const best = directional.reduce((m, k) => k.prio > m.prio ? k : m);
        if (best.prio >= prio) continue;
        directional.forEach(k => {
          if (k.a.star) return;
          const pos = kept.indexOf(k);
          if (pos >= 0) kept.splice(pos, 1);
        });
      }

      const item = { a, idx, prio };
      kept.push(item);
      lastLabel.set(label, { idx, prio, row: item });
    }

    return kept.sort((x, y) => x.idx - y.idx).map(x => x.a);
  }

  const originalLoad = AV.prototype.load;
  AV.prototype.load = async function () {
    await originalLoad.call(this);
    const filtered = filterAnnotations(this, this.annotations || []);
    if (filtered.length === (this.annotations || []).length) return;
    this.annotations = filtered;
    this._applyMarkers();
    this._requestRedraw();
  };

  AV.prototype.__qualityGateInstalled = true;
})();
