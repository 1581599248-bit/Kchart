/* signal_quality.js — 主图信号质量门控。 */
(function () {
  'use strict';

  const AV = window.AnalysisView;
  if (!AV) return;

  const CONFIRMED_PATTERN_LABELS = new Set([
    '突破颈线', '跌破颈线', '回测颈线', '向上突破', '向下跌破',
    '突破趋势', '跌破趋势', '结构失效', '扩散上破', '扩散下破',
  ]);

  const KIND_PRIORITY = {
    harmonic: 100,
    pattern: 90,
    divergence: 84,
    trend: 76,
    indicator: 70,
    fibonacci: 55,
  };

  function isPatternConfirmation(a) {
    return a.kind === 'pattern' && CONFIRMED_PATTERN_LABELS.has(String(a.label || ''));
  }

  function labelPriority(a) {
    let p = KIND_PRIORITY[a.kind] || 40;
    if (a.star) p += 30;
    if (a.history_label) p = 62;
    if (a.label === 'MACD顶背离' || a.label === 'MACD底背离') p += 6;
    if (a.label === '0.618') p += 5;
    if (a.label === '0.5') p += 3;
    if (a.label === '结构失效') p -= 8;
    return p;
  }

  function shouldKeepBasic(a, lastBarIdx, idx) {
    if (a.trace_only || a.history_label) return true;
    if (a.kind === 'pattern') {
      if (a.star || isPatternConfirmation(a)) return true;
      return false;
    }
    if (a.active === false && a.kind === 'indicator' && idx < lastBarIdx - 160) return false;
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

      if (a.trace_only || a.history_label || isPatternConfirmation(a)) {
        kept.push({ a, idx, prio });
        continue;
      }

      const previous = lastLabel.get(label);
      if (previous && idx - previous.idx < 30 && !a.star) {
        if (prio <= previous.prio) continue;
        const pos = kept.indexOf(previous.row);
        if (pos >= 0) kept.splice(pos, 1);
      }

      if (a.kind === 'fibonacci') {
        const conflict = kept.filter(k =>
          k.a.kind === 'fibonacci' && Math.abs(k.idx - idx) <= 2
        );
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

      const directional = kept.filter(k =>
        !k.a.trace_only
        && !k.a.history_label
        && !isPatternConfirmation(k.a)
        && k.a.kind !== 'pattern'
        && k.a.kind !== 'fibonacci'
        && a.kind !== 'pattern'
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

  function apply(view) {
    const current = view.annotations || [];
    view.annotations = filterAnnotations(view, current);
    view._applyMarkers();
    view._requestRedraw();
    return view.annotations;
  }

  window.SignalQuality = { filterAnnotations, apply, isPatternConfirmation };

  if (!AV.prototype.__qualityGateInstalled) {
    const originalLoad = AV.prototype.load;
    AV.prototype.load = async function () {
      await originalLoad.call(this);
      apply(this);
    };
    AV.prototype.__qualityGateInstalled = true;
  }
})();
