/* drawing.js — 画线工具：选择/直线/水平线/矩形/斐波那契回撤/多头盈亏比/删除
 * 用 series.attachPrimitive 自绘 canvas；两/三点交互（点击落点+移动预览），可选中删除、拖动端点；
 * 按 ts_code+timeframe 存 localStorage。
 */
(function () {
  'use strict';
  const GOLD = '#f0b90b', UP = '#ef5350', DOWN = '#26a69a', BLUE = '#4dc3ff', TEXT = '#d1d4dc';
  const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  const TOOLS = [
    { key: 'select', label: '选择' },
    { key: 'line', label: '直线', pts: 2 },
    { key: 'hline', label: '水平线', pts: 1 },
    { key: 'rect', label: '矩形', pts: 2 },
    { key: 'fib', label: '斐波那契', pts: 2 },
    { key: 'longrr', label: '多头盈亏', pts: 3 },
  ];

  let uid = 1;

  class DrawingManager {
    constructor(board) {
      this.board = board;
      this.chart = board.mainChart;
      this.series = board.candleSeries;
      this.el = board.mainEl;
      this.tool = 'select';
      this.drawings = [];
      this.pending = null;      // {type, pts:[...], hover:{time,price}}
      this.selectedId = null;
      this.dragging = null;     // {drawing, ptIndex}
      this._dirty = true;
      this._buildToolbar();
      this._attachPrimitive();
      this._bindEvents();
      this._raf();
    }

    _storageKey() { return `ryan_draw:${this.board.tsCode}:${this.board.timeframe}`; }

    _buildToolbar() {
      const bar = document.createElement('div');
      bar.className = 'draw-bar';
      bar.innerHTML = TOOLS.map(t => `<button class="draw-btn${t.key === 'select' ? ' active' : ''}" data-tool="${t.key}">${t.label}</button>`).join('') +
        `<button class="draw-btn danger" data-del>删除</button>` +
        `<button class="draw-btn danger" data-clear>清空</button>`;
      this.board.el.querySelector('.draw-slot').appendChild(bar);
      this.barEl = bar;
      bar.querySelectorAll('[data-tool]').forEach(btn => {
        btn.addEventListener('click', () => this.setTool(btn.dataset.tool));
      });
      bar.querySelector('[data-del]').addEventListener('click', () => this.deleteSelected());
      bar.querySelector('[data-clear]').addEventListener('click', () => this.clearAll());
    }

    setTool(tool) {
      this.tool = tool;
      this.pending = null;
      this.barEl.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('active', b.dataset.tool === tool));
      this._markDirty();
    }

    _attachPrimitive() {
      const self = this;
      this._requestUpdate = null;
      // LC v4.2 契约：primitive 需实现 paneViews() 返回带 renderer() 的视图，
      // 顶层 draw() 永远不会被调用
      const renderer = {
        draw(target) {
          target.useBitmapCoordinateSpace(scope => {
            const ctx = scope.context;
            const hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
            for (const d of self.drawings) self._render(ctx, hr, vr, d, d.id === self.selectedId);
            if (self.pending) {
              const p = Object.assign({}, self.pending);
              if (p.hover && p.pts.length < (self._toolDef(p.type).pts || 0)) p.pts = p.pts.concat([p.hover]);
              self._render(ctx, hr, vr, p, true);
            }
          });
        },
      };
      const view = { renderer: () => renderer };
      this.primitive = {
        attached(params) { if (params && params.requestUpdate) self._requestUpdate = params.requestUpdate; },
        paneViews() { return [view]; },
      };
      this.series.attachPrimitive(this.primitive);
      this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this._markDirty());
    }

    _markDirty() { this._dirty = true; }
    _raf() {
      const tick = () => {
        if (this._destroyed) return;
        if (this._dirty) {
          this._dirty = false;
          if (this._requestUpdate) this._requestUpdate();
          else this.chart.timeScale().applyOptions({});
        }
        this._rafId = requestAnimationFrame(tick);
      };
      this._rafId = requestAnimationFrame(tick);
    }

    _toolDef(key) { return TOOLS.find(t => t.key === key) || {}; }

    // ---- 坐标换算 ----
    _toXY(pt) {
      const x = this.chart.timeScale().timeToCoordinate(pt.time);
      const y = this.series.priceToCoordinate(pt.price);
      if (x == null || y == null) return null;
      return { x, y };
    }
    _snapTime(x) {
      const t = this.chart.timeScale().coordinateToTime(x);
      if (t == null) return null;
      const bars = this.board.bars;
      if (!bars.length) return t;
      // 吸附到最近 bar
      let best = bars[0].time, bd = Math.abs(bars[0].time - t);
      for (const b of bars) { const d = Math.abs(b.time - t); if (d < bd) { bd = d; best = b.time; } }
      return best;
    }
    _eventPoint(e) {
      const rect = this.el.getBoundingClientRect();
      const x = e.clientX - rect.left, y = e.clientY - rect.top;
      const time = this._snapTime(x);
      const price = this.series.coordinateToPrice(y);
      if (time == null || price == null) return null;
      return { time, price };
    }

    // ---- 鼠标交互 ----
    _bindEvents() {
      this.el.addEventListener('mousedown', e => this._onDown(e));
      this._moveFn = e => this._onMove(e);
      this._upFn = () => this._onUp();
      window.addEventListener('mousemove', this._moveFn);
      window.addEventListener('mouseup', this._upFn);
      this._keyFn = e => { if (e.key === 'Delete' || e.key === 'Backspace') this.deleteSelected(); };
      window.addEventListener('keydown', this._keyFn);
    }

    _onDown(e) {
      if (e.button !== 0) return;
      const pt = this._eventPoint(e);
      if (!pt) return;
      if (this.tool === 'select') {
        // 端点拖拽优先
        const hit = this._hitEndpoint(e);
        if (hit) { this.dragging = hit; this.selectedId = hit.drawing.id; this._markDirty(); return; }
        const d = this._hitDrawing(e);
        this.selectedId = d ? d.id : null;
        this._markDirty();
        return;
      }
      // 画线模式：落点
      if (!this.pending) this.pending = { type: this.tool, pts: [], hover: null };
      this.pending.pts.push(pt);
      const need = this._toolDef(this.tool).pts;
      if (this.pending.pts.length >= need) {
        this.drawings.push({ id: uid++, type: this.pending.type, pts: this.pending.pts.slice(0, need) });
        this.pending = null;
        this._save();
      }
      this._markDirty();
    }

    _onMove(e) {
      if (this.dragging) {
        const pt = this._eventPoint(e);
        if (pt) {
          this.dragging.drawing.pts[this.dragging.ptIndex] = pt;
          this._markDirty();
        }
        return;
      }
      const rect = this.el.getBoundingClientRect();
      const inside = e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;
      if (this.pending) {
        this.pending.hover = inside ? this._eventPoint(e) : null;
        this._markDirty();
      } else if (this.tool === 'select' && inside) {
        const hover = this._hitEndpoint(e) || this._hitDrawing(e);
        this.el.style.cursor = hover ? 'pointer' : '';
      }
    }

    _onUp() {
      if (this.dragging) { this.dragging = null; this._save(); this._markDirty(); }
    }

    // ---- 命中检测 ----
    _mouseXY(e) {
      const rect = this.el.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }
    _hitEndpoint(e) {
      const m = this._mouseXY(e);
      for (const d of this.drawings) {
        for (let i = 0; i < d.pts.length; i++) {
          const xy = this._toXY(d.pts[i]);
          if (xy && Math.hypot(xy.x - m.x, xy.y - m.y) < 9) return { drawing: d, ptIndex: i };
        }
      }
      return null;
    }
    _hitDrawing(e) {
      const m = this._mouseXY(e);
      for (let i = this.drawings.length - 1; i >= 0; i--) {
        if (this._hitTest(this.drawings[i], m)) return this.drawings[i];
      }
      return null;
    }
    _hitTest(d, m) {
      const T = 7;
      const pts = d.pts.map(p => this._toXY(p));
      if (pts.some(p => !p)) return false;
      if (d.type === 'hline') return Math.abs(pts[0].y - m.y) < T;
      if (d.type === 'line') return distToSeg(m, pts[0], pts[1]) < T;
      if (d.type === 'rect') {
        const [a, b] = pts;
        const x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x), y1 = Math.min(a.y, b.y), y2 = Math.max(a.y, b.y);
        const onX = m.x >= x1 - T && m.x <= x2 + T, onY = m.y >= y1 - T && m.y <= y2 + T;
        return onX && onY && (Math.abs(m.x - x1) < T || Math.abs(m.x - x2) < T || Math.abs(m.y - y1) < T || Math.abs(m.y - y2) < T);
      }
      if (d.type === 'fib') {
        const [a, b] = pts;
        const x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x);
        if (m.x < x1 - T || m.x > x2 + T) return false;
        return FIB_LEVELS.some(L => Math.abs(a.y + (b.y - a.y) * L - m.y) < T);
      }
      if (d.type === 'longrr' || d.type === 'shortrr') {
        const box = this._rrGeom(d, pts);
        if (!box) return false;
        return m.x >= box.x1 - T && m.x <= box.x2 + T && m.y >= Math.min(box.yT, box.yS) - T && m.y <= Math.max(box.yT, box.yS) + T;
      }
      return false;
    }

    _rrGeom(d, pts) {
      if (pts.length < 3) return null;
      const [e, p2, p3] = pts;
      const isLong = d.type === 'longrr';
      const yHi = Math.min(p2.y, p3.y), yLo = Math.max(p2.y, p3.y); // canvas y 向下
      const yT = isLong ? yHi : yLo, yS = isLong ? yLo : yHi;      // T=止盈 S=止损
      return { x1: Math.min(e.x, p2.x, p3.x), x2: Math.max(e.x, p2.x, p3.x), yE: e.y, yT, yS };
    }

    // ---- 渲染 ----
    _render(ctx, hr, vr, d, highlight) {
      const pts = d.pts.map(p => this._toXY(p));
      // 各类型最小点数守卫：pending 预览无 hover 时点数不足，直接跳过（防空指针）
      const need = { line: 2, hline: 1, rect: 2, fib: 2, longrr: 3, shortrr: 3 }[d.type] || 2;
      if (pts.length < need || pts.some(p => !p)) return;
      const X = v => v * hr, Y = v => v * vr;
      ctx.lineWidth = (highlight ? 2 : 1.2) * hr;
      ctx.strokeStyle = highlight ? GOLD : BLUE;
      ctx.fillStyle = highlight ? GOLD : BLUE;
      ctx.font = `${11 * vr}px "Microsoft YaHei", sans-serif`;

      if (d.type === 'line') {
        line(ctx, X(pts[0].x), Y(pts[0].y), X(pts[1].x), Y(pts[1].y));
      } else if (d.type === 'hline') {
        const w = ctx.canvas.width;
        dash(ctx, 0, Y(pts[0].y), w, Y(pts[0].y), hr);
        ctx.fillText(fmt(pts.length && d.pts[0].price), 6 * hr, Y(pts[0].y) - 4 * vr);
      } else if (d.type === 'rect') {
        const x1 = X(Math.min(pts[0].x, pts[1].x)), x2 = X(Math.max(pts[0].x, pts[1].x));
        const y1 = Y(Math.min(pts[0].y, pts[1].y)), y2 = Y(Math.max(pts[0].y, pts[1].y));
        ctx.fillStyle = highlight ? 'rgba(240,185,11,.10)' : 'rgba(77,195,255,.08)';
        ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      } else if (d.type === 'fib') {
        const x1 = X(Math.min(pts[0].x, pts[1].x)), x2 = X(Math.max(pts[0].x, pts[1].x));
        const p1 = d.pts[0].price, p2 = d.pts[1].price;
        for (const L of FIB_LEVELS) {
          const y = Y(pts[0].y + (pts[1].y - pts[0].y) * L);
          ctx.strokeStyle = L === 0.618 || L === 0.382 ? GOLD : (highlight ? GOLD : '#8896b3');
          dash(ctx, x1, y, x2, y, hr);
          ctx.fillStyle = ctx.strokeStyle;
          ctx.fillText(`${L}  ${fmt(p1 + (p2 - p1) * L)}`, x2 + 4 * hr, y - 3 * vr);
        }
      } else if (d.type === 'longrr' || d.type === 'shortrr') {
        const box = this._rrGeom(d, pts);
        if (!box) return;
        const x1 = X(box.x1), x2 = X(box.x2), yE = Y(box.yE), yT = Y(box.yT), yS = Y(box.yS);
        // 止盈红区（红涨）
        ctx.fillStyle = 'rgba(239,83,80,.15)';
        ctx.fillRect(x1, Math.min(yE, yT), x2 - x1, Math.abs(yT - yE));
        // 止损绿区（绿跌）
        ctx.fillStyle = 'rgba(38,166,154,.15)';
        ctx.fillRect(x1, Math.min(yE, yS), x2 - x1, Math.abs(yS - yE));
        ctx.strokeStyle = UP; dash(ctx, x1, yT, x2, yT, hr);
        ctx.strokeStyle = DOWN; dash(ctx, x1, yS, x2, yS, hr);
        ctx.strokeStyle = TEXT; dash(ctx, x1, yE, x2, yE, hr);
        // RR 标注
        const entry = d.pts[0].price;
        const others = d.pts.slice(1).map(p => p.price);
        const tp = d.type === 'longrr' ? Math.max(...others) : Math.min(...others);
        const sl = d.type === 'longrr' ? Math.min(...others) : Math.max(...others);
        const risk = Math.abs(entry - sl);
        const rr = risk > 0 ? Math.abs(tp - entry) / risk : 0;
        ctx.fillStyle = GOLD;
        ctx.fillText(`RR ${rr.toFixed(2)}  入场${fmt(entry)} 止盈${fmt(tp)} 止损${fmt(sl)}`, x1 + 4 * hr, Math.min(yT, yS) - 5 * vr);
      }
      // 端点
      if (highlight) {
        ctx.fillStyle = GOLD;
        for (const p of pts) { ctx.beginPath(); ctx.arc(X(p.x), Y(p.y), 3.5 * hr, 0, Math.PI * 2); ctx.fill(); }
      }
    }

    // ---- 持久化 ----
    _save() {
      try { localStorage.setItem(this._storageKey(), JSON.stringify(this.drawings)); } catch (_) { /* 忽略 */ }
    }
    _restore() {
      // 一次性迁移 v2：应用户要求清除历史残留的全部“线”类画线（斐波那契/水平线/直线），
      // 只保留矩形与盈亏比这类可能是有意保存的规划图形
      try {
        if (!localStorage.getItem('ryan_draw_purge_v2')) {
          for (const k of Object.keys(localStorage)) {
            if (!k.startsWith('ryan_draw:')) continue;
            try {
              const arr = JSON.parse(localStorage.getItem(k) || '[]');
              const kept = arr.filter(d => d && !['fib', 'hline', 'line'].includes(d.type));
              if (kept.length) localStorage.setItem(k, JSON.stringify(kept));
              else localStorage.removeItem(k);
            } catch (_) { localStorage.removeItem(k); }
          }
          localStorage.setItem('ryan_draw_purge_v2', '1');
        }
      } catch (_) { /* 忽略 */ }
      try {
        const raw = localStorage.getItem(this._storageKey());
        this.drawings = raw ? JSON.parse(raw) : [];
        uid = Math.max(uid, ...this.drawings.map(d => d.id + 1), 1);
      } catch (_) { this.drawings = []; }
      this.selectedId = null;
      this.pending = null;
      this._markDirty();
    }

    deleteSelected() {
      if (this.selectedId == null) { window.API.toast('请先在“选择”模式下点击要删除的画线'); return; }
      this.drawings = this.drawings.filter(d => d.id !== this.selectedId);
      this.selectedId = null;
      this._save();
      this._markDirty();
    }

    clearAll() {
      if (!this.drawings.length) { window.API.toast('当前没有画线'); return; }
      if (!window.confirm(`确定清除当前图上的全部 ${this.drawings.length} 条画线？`)) return;
      this.drawings = [];
      this.selectedId = null;
      this._save();
      this._markDirty();
    }

    onDataLoaded() { this._restore(); }

    destroy() {
      this._destroyed = true;
      if (this._rafId) cancelAnimationFrame(this._rafId);
      window.removeEventListener('mousemove', this._moveFn);
      window.removeEventListener('mouseup', this._upFn);
      window.removeEventListener('keydown', this._keyFn);
      try { this.series.detachPrimitive(this.primitive); } catch (_) { /* 忽略 */ }
    }
  }

  function line(ctx, x1, y1, x2, y2) { ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }
  function dash(ctx, x1, y1, x2, y2, hr) {
    ctx.save(); ctx.setLineDash([5 * hr, 4 * hr]); line(ctx, x1, y1, x2, y2); ctx.restore();
  }
  function distToSeg(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y;
    const L2 = dx * dx + dy * dy;
    if (L2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
    let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
  }
  function fmt(v) { return v == null ? '--' : (+v).toFixed(2); }

  window.DrawingManager = DrawingManager;
})();
