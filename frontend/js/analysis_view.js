/* analysis_view.js — 推背图：标注、结构描摹、hover详情与分析卡。 */
(function () {
  'use strict';
  const GOLD = '#f0b90b', UP = '#ef5350', DOWN = '#26a69a', DIMC = '#8896b3';

  class AnalysisView {
    constructor(board, opts) {
      this.board = board;
      this.showSummary = !opts || opts.summary !== false;
      this.quiet = !!(opts && opts.quiet);
      this.chart = board.mainChart;
      this.series = board.candleSeries;
      this.data = null;
      this.annotations = [];
      this._buildTip();
      this._attachOverlay();
      this.chart.subscribeCrosshairMove(p => this._onHover(p));
    }

    async load() {
      if (!this.board.tsCode) return;
      const slot = this.board.el.querySelector('.analysis-slot');
      if (this.showSummary) slot.innerHTML = '<div class="analysis-card dim skeleton">推背图分析计算中…</div>';
      try {
        const dataP = window.API.analysis(this.board.tsCode, this.board.timeframe, false);
        if (this.board._barsReady) await this.board._barsReady;
        this.data = await dataP;
        this.annotations = (this.data.annotations || []).map(a => this._normalize(a)).filter(a => a.time != null);
        this._barByTime = new Map(this.board.bars.map(b => [b.time, b]));
        this._applyMarkers();
        if (this.showSummary) this._renderSummary(this.data.summary || {});
        this._requestRedraw();
      } catch (e) {
        if (this.showSummary) slot.innerHTML = `<div class="analysis-card dim">分析暂不可用：${e.message}</div>`;
      }
    }

    _normalize(a) {
      const out = Object.assign({}, a);
      if (out.time == null && out.bar_idx != null) {
        const b = this.board.bars[out.bar_idx];
        if (b) out.time = b.time;
      }
      return out;
    }

    _applyMarkers() {
      const markers = [];
      for (const a of this.annotations) {
        if (a.trace_only) continue;
        if (this.quiet && !a.star) continue;
        const bull = a.direction === 'bull', bear = a.direction === 'bear';
        markers.push({
          time: a.time, position: bull ? 'belowBar' : 'aboveBar',
          color: a.star ? GOLD : bull ? UP : bear ? DOWN : DIMC,
          // 箭头统一最小号：星标与非星标都不抢戏，★已在文字标签里
          size: 1,
          shape: bull ? 'arrowUp' : bear ? 'arrowDown' : 'circle',
          text: '',
        });
      }
      markers.sort((x, y) => x.time - y.time);
      this.series.setMarkers(markers);
    }

    _textSpec(a) {
      const bull = a.direction === 'bull', bear = a.direction === 'bear';
      const prefix = a.kind === 'fibonacci' ? '' : bull ? '看多 ' : bear ? '看跌 ' : '中性 ';
      const kindW = { divergence: 0, harmonic: 1, pattern: 1, indicator: 2, trend: 3 }[a.kind] ?? 4;
      return {
        text: (a.star ? '★' : '') + prefix + (a.label || ''),
        color: a.star ? GOLD : bull ? UP : bear ? DOWN : DIMC,
        prio: (a.star ? 0 : 10) + kindW,
        bold: !!a.star,
        below: bull,
      };
    }

    _attachOverlay() {
      const self = this;
      this._requestUpdate = null;
      const renderer = {
        draw(target) {
          if (!self.annotations.length) return;
          target.useBitmapCoordinateSpace(scope => {
            const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
            const toX = t => self.chart.timeScale().timeToCoordinate(t);
            const toY = p => self.series.priceToCoordinate(p);
            const cssW = scope.bitmapSize.width / hr;
            const small = cssW < 640;   // 窄屏（手机）字号再降一档，但永不消失
            const frameBoxes = [];      // 本帧全部占位盒：描摹标签 + 文字标签共用碰撞池
            for (const a of self.annotations) {
              const dead = a.active === false;
              const polylines = a.trace_only ? (a.polylines || []) : (dead ? [] : (a.polylines || []));
              for (const pl of polylines) {
                const pts = (pl.points || []).map(pt => ({ x: toX(pt.t), y: toY(pt.p), p: pt.p }))
                  .filter(p => p.x != null && p.y != null);
                if (pts.length < 2) continue;
                ctx.save();
                ctx.globalAlpha = a.trace_only ? 0.95 : 1;
                ctx.strokeStyle = pl.color || GOLD;
                ctx.lineJoin = 'round';
                ctx.lineCap = 'round';
                // 大结构（金线）粗描，小结构（紫线）细描
                const big = (pl.color || GOLD) === GOLD;
                ctx.lineWidth = (a.trace_only
                  ? (big ? (pl.style === 'dashed' ? 2.0 : 2.8) : (pl.style === 'dashed' ? 1.2 : 1.5))
                  : 1.8) * hr;
                if (pl.style === 'dashed') ctx.setLineDash([5 * hr, 4 * hr]);
                ctx.beginPath();
                ctx.moveTo(pts[0].x * hr, pts[0].y * vr);
                for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x * hr, pts[i].y * vr);
                ctx.stroke();
                if (a.trace_only && pts.length >= 2) {
                  const fmtP = (v) => v >= 1000 ? String(Math.round(v)) : (+v).toFixed(2);
                  const maxX = scope.bitmapSize.width, maxY = scope.bitmapSize.height;
                  const chip = (text, cx, cy, baseline) => {
                    const w = ctx.measureText(text).width;
                    const h = 8 * vr;
                    const x1 = ctx.textAlign === 'right' ? cx - w : ctx.textAlign === 'center' ? cx - w / 2 : cx;
                    const y1 = baseline === 'top' ? cy : baseline === 'bottom' ? cy - h : cy - h / 2;
                    ctx.fillStyle = 'rgba(13,17,23,0.72)';
                    ctx.fillRect(x1 - 1.5 * hr, y1 - 1 * vr, w + 3 * hr, h + 2 * vr);
                    frameBoxes.push({ x1: x1 - 2 * hr, x2: x1 + w + 2 * hr, y1: y1 - 2 * vr, y2: y1 + h + 2 * vr });
                  };
                  if (pl.style === 'dashed') {
                    // 颈线数值标在虚线右端：空头在下方，多头在上方，不飞出图
                    const rp = pts[pts.length - 1];
                    ctx.font = ((small ? 6.5 : 7.5) * hr) + "px 'Trebuchet MS', sans-serif";
                    ctx.textAlign = 'right';
                    ctx.textBaseline = a.direction === 'bear' ? 'top' : 'bottom';
                    const ny = Math.max(10 * vr, Math.min(rp.y * vr + (a.direction === 'bear' ? 3 : -3) * vr, maxY - 10 * vr));
                    const nx = Math.min(rp.x * hr - 4 * hr, maxX - 4 * hr);
                    chip('颈线 ' + fmtP(rp.p), nx, ny, ctx.textBaseline);
                    ctx.fillStyle = pl.color || GOLD;
                    ctx.fillText('颈线 ' + fmtP(rp.p), nx, ny);
                    ctx.textAlign = 'center';
                  } else if (pl.style === 'solid' && pts.length >= 4) {
                    // 关键拐点价位：跳过起手腿端点(0)与突破腿终点(n-1)；与颈线同值的拐点（中间谷/峰）
                    // 不重复标价（颈线标签已携带该数值）——否则两字重叠成"颈线04003"之类的叠影
                    const neckPl = (a.polylines || []).find(q => q.style === 'dashed');
                    const neckP = neckPl && neckPl.points && neckPl.points.length ? +neckPl.points[0].p : null;
                    ctx.font = ((small ? 6.5 : 7.5) * hr) + "px 'Trebuchet MS', sans-serif";
                    ctx.textAlign = 'center';
                    for (let i = 1; i < pts.length - 1; i++) {
                      if (neckP != null && Math.abs(pts[i].p - neckP) / neckP < 0.002) continue;
                      const isTop = pts[i].p >= pts[i - 1].p && pts[i].p >= pts[i + 1].p;
                      ctx.textBaseline = isTop ? 'bottom' : 'top';
                      const vy = Math.max(9 * vr, Math.min(pts[i].y * vr + (isTop ? -2.5 : 2.5) * vr, maxY - 2 * vr));
                      chip(fmtP(pts[i].p), pts[i].x * hr, vy, ctx.textBaseline);
                      ctx.fillStyle = 'rgba(139,152,182,.95)';
                      ctx.fillText(fmtP(pts[i].p), pts[i].x * hr, vy);
                    }
                  }
                }
                ctx.restore();
              }
              const lineCol = a.star ? GOLD
                : a.direction === 'bull' ? UP : a.direction === 'bear' ? DOWN : DIMC;
              for (const ln of (dead || a.trace_only ? [] : (a.lines || []))) {
                const x1 = toX(ln.t1), x2 = toX(ln.t2), y1 = toY(ln.p1), y2 = toY(ln.p2);
                if ([x1, x2, y1, y2].some(v => v == null)) continue;
                ctx.strokeStyle = lineCol;
                ctx.lineWidth = 1.2 * hr;
                ctx.save();
                if (ln.style === 'dashed') ctx.setLineDash([6 * hr, 4 * hr]);
                ctx.beginPath(); ctx.moveTo(x1 * hr, y1 * vr); ctx.lineTo(x2 * hr, y2 * vr); ctx.stroke();
                ctx.restore();
              }
              for (const z of (dead || a.trace_only ? [] : (a.zones || []))) {
                const x1 = toX(z.t1), x2 = toX(z.t2), yT = toY(z.top), yB = toY(z.bottom);
                if ([x1, x2, yT, yB].some(v => v == null)) continue;
                ctx.fillStyle = z.color || 'rgba(136,150,179,.08)';
                ctx.fillRect(Math.min(x1, x2) * hr, Math.min(yT, yB) * vr,
                  Math.abs(x2 - x1) * hr, Math.abs(yB - yT) * vr);
              }
            }
            if (!self.quiet) {
              const vrng = self.chart.timeScale().getVisibleRange();
              if (vrng && vrng.from != null && vrng.to != null) {
                const cand = [];
                for (const a of self.annotations) {
                  if (a.trace_only || a.time < vrng.from || a.time > vrng.to) continue;
                  const x = toX(a.time);
                  const b = self._barByTime ? self._barByTime.get(a.time) : null;
                  // 锚定到箭头针对的那根 K 线本体：空头取最高、多头取最低，
                  // 标签贴着箭头走，不漂到别的价位/别的 K 线上
                  const anchorP = b ? (a.direction === 'bull' ? b.low : b.high)
                    : (a.price != null ? a.price : null);
                  if (x == null || anchorP == null) continue;
                  const y = toY(anchorP);
                  if (y == null) continue;
                  cand.push({ a, x, y, spec: self._textSpec(a) });
                }
                cand.sort((p, q) => p.spec.prio - q.spec.prio || p.x - q.x);
                ctx.textAlign = 'center';
                const PAD = 3, TH = 10, placed = frameBoxes, maxX = scope.bitmapSize.width;
                for (const c of cand) {
                  // 整体再小一号：桌面 8.5/7.5px，手机 7.5/7px —— 小但不消失
                  const s = c.spec, px = small ? (s.bold ? 7.5 : 7) : (s.bold ? 8.5 : 7.5);
                  ctx.font = (s.bold ? 'bold ' : '') + (px * hr) + "px 'Trebuchet MS', sans-serif";
                  const w = ctx.measureText(s.text).width;
                  let cx = c.x * hr;
                  cx = Math.max(w / 2 + 2, Math.min(cx, maxX - w / 2 - 2));
                  const gap = (s.bold ? 17 : 13) * vr;
                  const y0 = s.below ? (c.y * vr + gap) : (c.y * vr - gap - TH * vr);
                  // 标签碰撞：首选侧最多 3 层，再换对侧最多 3 层；实在没空位就取重叠最少
                  // 的位置——永不丢弃（用户要求字不因挤压消失），同时钳在图区内不飞天
                  const paneH = scope.bitmapSize.height;
                  const mkBox = ly => ({ x1: cx - w / 2 - PAD * hr, x2: cx + w / 2 + PAD * hr, y1: ly - PAD * vr, y2: ly + TH * vr + PAD * vr });
                  const hits = b => placed.reduce((n, b2) => n + ((b.x1 < b2.x2 && b.x2 > b2.x1 && b.y1 < b2.y2 && b.y2 > b2.y1) ? 1 : 0), 0);
                  const y0flip = s.below ? (c.y * vr - gap - TH * vr) : (c.y * vr + gap);
                  let ly = null, bestLy = y0, bestHits = Infinity;
                  for (let side = 0; side < 2 && ly === null; side++) {
                    const base = side === 0 ? y0 : y0flip;
                    const dir = (side === 0 ? s.below : !s.below) ? 1 : -1;
                    for (let shift = 0; shift <= 3; shift++) {
                      let candY = base + dir * shift * (TH + 2) * vr;
                      candY = Math.max(2, Math.min(candY, paneH - (TH + 2) * vr - 2));
                      const h = hits(mkBox(candY));
                      if (h === 0) { ly = candY; break; }
                      if (h < bestHits) { bestHits = h; bestLy = candY; }
                    }
                  }
                  if (ly === null) ly = bestLy;
                  placed.push(mkBox(ly));
                  ctx.fillStyle = 'rgba(13,17,23,0.72)';
                  ctx.fillRect(cx - w / 2 - 2 * hr, ly - 1 * vr, w + 4 * hr, TH * vr + 2 * vr);
                  ctx.fillStyle = s.color;
                  ctx.textBaseline = s.below ? 'top' : 'bottom';
                  ctx.fillText(s.text, cx, s.below ? ly : ly + TH * vr);
                }
              }
            }
            self._placedBoxes = frameBoxes;
          });
        },
      };
      const view = { renderer: () => renderer };
      this.overlay = {
        attached(params) { if (params && params.requestUpdate) self._requestUpdate = params.requestUpdate; },
        paneViews() { return [view]; },
      };
      this.series.attachPrimitive(this.overlay);
      this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this._requestRedraw());
    }

    _requestRedraw() {
      if (this._requestUpdate) this._requestUpdate();
      else this.chart.timeScale().applyOptions({});
    }

    _buildTip() {
      this.tip = document.createElement('div');
      this.tip.className = 'anno-tip';
      this.board.el.querySelector('.kl-body').appendChild(this.tip);
    }

    _onHover(param) {
      if (!param || !param.time || !param.point || !this.annotations.length) { this.tip.style.display = 'none'; return; }
      const t = param.time;
      let best = null, bd = Infinity;
      for (const a of this.annotations) {
        if (a.trace_only) continue;
        const d = Math.abs(a.time - t);
        if (d < bd) { bd = d; best = a; }
      }
      if (!best) { this.tip.style.display = 'none'; return; }
      const bars = this.board.bars;
      const gap = bars.length > 1 ? Math.abs(bars[bars.length - 1].time - bars[bars.length - 2].time) : 86400;
      if (bd > gap * 1.5) { this.tip.style.display = 'none'; return; }
      const prefix = best.kind === 'fibonacci' ? ''
        : best.direction === 'bull' ? '看多 ' : best.direction === 'bear' ? '看跌 ' : '中性 ';
      this.tip.innerHTML = `<b class="gold">${best.star ? '★' : ''}${prefix}${best.label || best.kind}</b><br>${best.detail || ''}`;
      this.tip.style.display = 'block';
      const x = Math.min(param.point.x + 16, this.board.mainEl.clientWidth - 330);
      this.tip.style.left = Math.max(4, x) + 'px';
      this.tip.style.top = Math.max(4, param.point.y - 10) + 'px';
    }

    _renderSummary(s) {
      const slot = this.board.el.querySelector('.analysis-slot');
      const arr = v => Array.isArray(v) ? v.map(x => (+x).toFixed(2)).join(' / ') : (v || '—');
      const num = v => v == null ? '—' : (+v).toFixed(2);
      const item = (k, inner) => `<p class="item"><span class="k">${k}</span>${inner || '—'}</p>`;
      const tgt = s.target_price != null
        ? `<span class="up">${num(s.target_price)}</span>${s.target_source ? `（${s.target_source}）` : ''}`
        : null;
      const stp = s.stop_loss != null
        ? `<span class="down">${num(s.stop_loss)}</span>${s.stop_source ? `（${s.stop_source}）` : ''}`
        : null;
      const outlookHtml = String(s.outlook_text || '')
        .split(/(?<=。)/).map(x => x.trim()).filter(Boolean)
        .map(x => `<p>${x}</p>`).join('');
      slot.innerHTML = `
        <div class="analysis-card">
          ${item('趋势', s.trend)}
          ${item('结构', s.structure)}
          ${item('动量', s.momentum)}
          ${item('量能', s.volume)}
          ${item('关键位', `支撑 <span class="up">${arr(s.key_supports)}</span>　阻力 <span class="down">${arr(s.key_resistances)}</span>`)}
          ${item('目标/止损', `${tgt || '目标 —'}　${stp || '止损 —'}　盈亏比 <span class="gold">${num(s.risk_reward)}</span>`)}
          <div class="outlook">${outlookHtml}</div>
        </div>`;
    }
  }

  window.AnalysisView = AnalysisView;
})();
