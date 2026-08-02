/* analysis_view.js — 推背图：/api/analysis 的 annotations 渲染成图上箭头标记（星标金色大号）+
 * 结构线/区域色带（attachPrimitive 自绘）+ hover detail + 图下分析卡。
 * 标注箭头常驻；文字由 overlay 自绘，按优先级（星标>背离/形态/谐波>指标>趋势）贪心防碰撞：
 * 任何缩放级别下重要信号文字优先保留，放不下的文字自动省略（箭头仍在），hover 详情不受影响。
 */
(function () {
  'use strict';
  const GOLD = '#f0b90b', UP = '#ef5350', DOWN = '#26a69a', DIMC = '#8896b3';

  class AnalysisView {
    constructor(board, opts) {
      this.board = board;
      this.showSummary = !opts || opts.summary !== false;
      this.quiet = !!(opts && opts.quiet);   // 迷你图：标注只留箭头，永不挂文字
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
        // 分析窗口与已加载K线区间对齐：pan/zoom 到任何历史位置都有标注
        const first = this.board.bars[0];
        const start = first ? new Date(first.time * 1000).toISOString().slice(0, 10) : undefined;
        this.data = await window.API.analysis(this.board.tsCode, this.board.timeframe, false, start);
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

    // ---- 标记（箭头常驻：pan/zoom 不改变其存在性，星标金色大号；文字由 overlay 自绘防碰撞） ----
    _applyMarkers() {
      const markers = [];
      for (const a of this.annotations) {
        if (this.quiet && !a.star) continue;   // 迷你图：只留星标箭头，避免箭头汤
        const bull = a.direction === 'bull', bear = a.direction === 'bear';
        markers.push({
          time: a.time, position: bull ? 'belowBar' : 'aboveBar',
          color: a.star ? GOLD : bull ? UP : bear ? DOWN : DIMC,
          size: a.star ? (this.quiet ? 2 : 3) : 1,
          shape: bull ? 'arrowUp' : bear ? 'arrowDown' : 'circle',
          text: '',
        });
      }
      markers.sort((x, y) => x.time - y.time);
      this.series.setMarkers(markers);
    }

    // 文字渲染规格：内容 / 颜色 / 优先级（数值越小越重要，防碰撞时优先保留）
    _textSpec(a) {
      const bull = a.direction === 'bull', bear = a.direction === 'bear';
      const prefix = bull ? '看多 ' : bear ? '看跌 ' : '中性 ';
      const kindW = { divergence: 0, harmonic: 1, pattern: 1, indicator: 2, trend: 3 }[a.kind] ?? 4;
      return {
        text: (a.star ? '★' : '') + prefix + (a.label || ''),
        color: a.star ? GOLD : bull ? UP : bear ? DOWN : DIMC,
        prio: (a.star ? 0 : 10) + kindW,
        bold: !!a.star,
        below: bull,
      };
    }

    // ---- 结构线与区域 ----
    _attachOverlay() {
      const self = this;
      this._requestUpdate = null;
      // LC v4.2 契约：primitive 需实现 paneViews() 返回带 renderer() 的视图，
      // 顶层 draw() 永远不会被调用
      const renderer = {
        draw(target) {
          if (!self.annotations.length) return;
          target.useBitmapCoordinateSpace(scope => {
            const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
            const toX = t => self.chart.timeScale().timeToCoordinate(t);
            const toY = p => self.series.priceToCoordinate(p);
            for (const a of self.annotations) {
              // 失效（历史）形态：保留箭头标注，但不再画描摹线/颈线/区域（避免满屏旧灰线）
              const dead = a.active === false;
              // 形态描摹折线（金色实线描边 / 虚线边界）
              for (const pl of (dead ? [] : (a.polylines || []))) {
                const pts = (pl.points || []).map(pt => ({ x: toX(pt.t), y: toY(pt.p) }))
                  .filter(p => p.x != null && p.y != null);
                if (pts.length < 2) continue;
                ctx.strokeStyle = GOLD;
                ctx.lineWidth = 1.5 * hr;
                ctx.save();
                if (pl.style === 'dashed') ctx.setLineDash([5 * hr, 4 * hr]);
                ctx.beginPath();
                ctx.moveTo(pts[0].x * hr, pts[0].y * vr);
                for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x * hr, pts[i].y * vr);
                ctx.stroke();
                ctx.restore();
              }
              // 颈线/背离连线：星标金色；非星标按方向着色（多看涨红 / 看跌绿），不再用灰色
              const lineCol = a.star ? GOLD
                : a.direction === 'bull' ? UP : a.direction === 'bear' ? DOWN : DIMC;
              for (const ln of (dead ? [] : (a.lines || []))) {
                const x1 = toX(ln.t1), x2 = toX(ln.t2), y1 = toY(ln.p1), y2 = toY(ln.p2);
                if ([x1, x2, y1, y2].some(v => v == null)) continue;
                ctx.strokeStyle = lineCol;
                ctx.lineWidth = 1.2 * hr;
                ctx.save();
                if (ln.style === 'dashed') ctx.setLineDash([6 * hr, 4 * hr]);
                ctx.beginPath(); ctx.moveTo(x1 * hr, y1 * vr); ctx.lineTo(x2 * hr, y2 * vr); ctx.stroke();
                ctx.restore();
              }
              for (const z of (dead ? [] : (a.zones || []))) {
                const x1 = toX(z.t1), x2 = toX(z.t2), yT = toY(z.top), yB = toY(z.bottom);
                if ([x1, x2, yT, yB].some(v => v == null)) continue;
                ctx.fillStyle = z.color || 'rgba(136,150,179,.08)';
                ctx.fillRect(Math.min(x1, x2) * hr, Math.min(yT, yB) * vr,
                  Math.abs(x2 - x1) * hr, Math.abs(yB - yT) * vr);
              }
            }
            // 标注文字：按优先级贪心防碰撞（箭头由 series markers 绘制，这里只补文字）
            // 缩得越小，放不下的低优先级文字自动省略；星标>背离/形态/谐波>指标>趋势 优先保留
            if (!self.quiet) {
              const vrng = self.chart.timeScale().getVisibleRange();
              if (vrng && vrng.from != null && vrng.to != null) {
                const cand = [];
                for (const a of self.annotations) {
                  if (a.time < vrng.from || a.time > vrng.to) continue;
                  const x = toX(a.time);
                  const b = self._barByTime ? self._barByTime.get(a.time) : null;
                  const anchorP = a.price != null ? a.price
                    : b ? (a.direction === 'bull' ? b.low : b.high) : null;
                  if (x == null || anchorP == null) continue;
                  const y = toY(anchorP);
                  if (y == null) continue;
                  cand.push({ a, x, y, spec: self._textSpec(a) });
                }
                cand.sort((p, q) => p.spec.prio - q.spec.prio || p.x - q.x);
                ctx.textAlign = 'center';
                const PAD = 4, TH = 12, placed = [], maxX = scope.bitmapSize.width;
                for (const c of cand) {
                  const s = c.spec, px = s.bold ? 11 : 10;
                  ctx.font = (s.bold ? 'bold ' : '') + (px * hr) + "px 'Trebuchet MS', sans-serif";
                  const w = ctx.measureText(s.text).width;
                  let cx = c.x * hr;
                  cx = Math.max(w / 2 + 2, Math.min(cx, maxX - w / 2 - 2));
                  const gap = (s.bold ? 30 : 22) * vr;
                  const y1 = s.below ? (c.y * vr + gap) : (c.y * vr - gap - TH * vr);
                  const box = { x1: cx - w / 2 - PAD * hr, x2: cx + w / 2 + PAD * hr, y1: y1 - PAD * vr, y2: y1 + TH * vr + PAD * vr };
                  if (placed.some(b2 => box.x1 < b2.x2 && box.x2 > b2.x1 && box.y1 < b2.y2 && box.y2 > b2.y1)) continue;
                  placed.push(box);
                  // 深色底衬小片，避免文字被箭头/K线遮挡时不可读
                  ctx.fillStyle = 'rgba(13,17,23,0.72)';
                  ctx.fillRect(cx - w / 2 - 2 * hr, y1 - 1 * vr, w + 4 * hr, TH * vr + 2 * vr);
                  ctx.fillStyle = s.color;
                  ctx.textBaseline = s.below ? 'top' : 'bottom';
                  ctx.fillText(s.text, cx, s.below ? y1 : y1 + TH * vr);
                }
              }
            }
          });
        },
      };
      const view = { renderer: () => renderer };
      this.overlay = {
        attached(params) { if (params && params.requestUpdate) self._requestUpdate = params.requestUpdate; },
        paneViews() { return [view]; },
      };
      this.series.attachPrimitive(this.overlay);
      this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
        this._requestRedraw();
      });
    }
    _requestRedraw() {
      if (this._requestUpdate) this._requestUpdate();
      else this.chart.timeScale().applyOptions({});
    }

    // ---- hover detail ----
    _buildTip() {
      this.tip = document.createElement('div');
      this.tip.className = 'anno-tip';
      this.board.el.querySelector('.kl-body').appendChild(this.tip);
    }
    _onHover(param) {
      if (!param || !param.time || !param.point || !this.annotations.length) { this.tip.style.display = 'none'; return; }
      // 找时间上最近的标注（容差：相邻 bar 间隔的一半）
      const t = param.time;
      let best = null, bd = Infinity;
      for (const a of this.annotations) { const d = Math.abs(a.time - t); if (d < bd) { bd = d; best = a; } }
      if (!best) { this.tip.style.display = 'none'; return; }
      const bars = this.board.bars;
      const gap = bars.length > 1 ? Math.abs(bars[bars.length - 1].time - bars[bars.length - 2].time) : 86400;
      if (bd > gap * 1.5) { this.tip.style.display = 'none'; return; }
      this.tip.innerHTML = `<b class="gold">${best.star ? '★' : ''}${best.direction === 'bull' ? '看多 ' : best.direction === 'bear' ? '看跌 ' : '中性 '}${best.label || best.kind}</b><br>${best.detail || ''}`;
      this.tip.style.display = 'block';
      const x = Math.min(param.point.x + 16, this.board.mainEl.clientWidth - 330);
      this.tip.style.left = Math.max(4, x) + 'px';
      this.tip.style.top = Math.max(4, param.point.y - 10) + 'px';
    }

    // ---- 图下分析卡 ----
    _renderSummary(s) {
      const slot = this.board.el.querySelector('.analysis-slot');
      const arr = v => Array.isArray(v) ? v.map(x => (+x).toFixed(2)).join(' / ') : (v || '—');
      const num = v => v == null ? '—' : (+v).toFixed(2);
      // 每项独立成段，避免拥挤
      const item = (k, inner) => `<p class="item"><span class="k">${k}</span>${inner || '—'}</p>`;
      const tgt = s.target_price != null
        ? `<span class="up">${num(s.target_price)}</span>${s.target_source ? `（${s.target_source}）` : ''}`
        : null;
      const stp = s.stop_loss != null
        ? `<span class="down">${num(s.stop_loss)}</span>${s.stop_source ? `（${s.stop_source}）` : ''}`
        : null;
      // outlook 按「。」分段成 <p> 段落
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
