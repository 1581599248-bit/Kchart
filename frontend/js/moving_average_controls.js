/* moving_average_controls.js — MA/EMA主图均线开关。
 *
 * MA5/10/20/60与EMA20/60全部放入指标区，默认关闭；用户可逐条开启，
 * 选择保存在浏览器本地。均线数据仍由后端正常计算，仅控制主图显示。
 */
(function () {
  'use strict';

  const proto = window.KLineBoard && window.KLineBoard.prototype;
  if (!proto || proto.__movingAverageControlsInstalled) return;

  const STORAGE_KEY = 'ryan:kline:moving-averages:v1';
  const DEFINITIONS = [
    { key: 'ma5', label: 'MA5' },
    { key: 'ma10', label: 'MA10' },
    { key: 'ma20', label: 'MA20' },
    { key: 'ma60', label: 'MA60' },
    { key: 'ema20', label: 'EMA20' },
    { key: 'ema60', label: 'EMA60' },
  ];
  const VALID_KEYS = new Set(DEFINITIONS.map(item => item.key));

  function loadEnabled() {
    try {
      const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]');
      return Array.isArray(raw) ? raw.filter(key => VALID_KEYS.has(key)) : [];
    } catch (_) {
      return [];
    }
  }

  function saveEnabled(enabled) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...enabled]));
    } catch (_) { /* 隐私模式或存储受限时仅维持当前页面状态 */ }
  }

  function ensureState(board) {
    if (board.enabledMovingAverages instanceof Set) return;
    board.enabledMovingAverages = new Set(board.mini ? [] : loadEnabled());
  }

  function allSeries(board) {
    return Object.assign({}, board._maSeries || {}, board._emaSeries || {});
  }

  function applyVisibility(board) {
    ensureState(board);
    const series = allSeries(board);
    for (const item of DEFINITIONS) {
      if (!series[item.key]) continue;
      const visible = !board.mini && board.enabledMovingAverages.has(item.key);
      series[item.key].applyOptions({ visible });
    }
  }

  function syncControls(board) {
    if (!board.el) return;
    board.el.querySelectorAll('[data-moving-average]').forEach(cb => {
      cb.checked = board.enabledMovingAverages.has(cb.dataset.movingAverage);
    });
  }

  function broadcast(source) {
    if (!document || !document.querySelectorAll) return;
    document.querySelectorAll('.kl-board').forEach(root => {
      const board = root.klBoard;
      if (!board || board === source || board.mini) return;
      board.enabledMovingAverages = new Set(source.enabledMovingAverages);
      syncControls(board);
      applyVisibility(board);
    });
  }

  const originalBuildDom = proto._buildDom;
  proto._buildDom = function () {
    ensureState(this);
    originalBuildDom.call(this);
    if (this.mini) return;

    const group = this.el.querySelector('.ind-group');
    if (!group || group.querySelector('[data-moving-average]')) return;

    const fragment = document.createDocumentFragment();
    const title = document.createElement('span');
    title.className = 'ma-control-title';
    title.textContent = '均线';
    fragment.appendChild(title);

    for (const item of DEFINITIONS) {
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.movingAverage = item.key;
      checkbox.checked = this.enabledMovingAverages.has(item.key);
      label.appendChild(checkbox);
      label.appendChild(document.createTextNode(' ' + item.label));
      fragment.appendChild(label);
    }

    const divider = document.createElement('span');
    divider.className = 'ma-control-divider';
    divider.setAttribute('aria-hidden', 'true');
    fragment.appendChild(divider);
    group.insertBefore(fragment, group.firstChild);

    group.querySelectorAll('[data-moving-average]').forEach(cb => {
      cb.addEventListener('change', () => {
        const key = cb.dataset.movingAverage;
        if (cb.checked) this.enabledMovingAverages.add(key);
        else this.enabledMovingAverages.delete(key);
        saveEnabled(this.enabledMovingAverages);
        applyVisibility(this);
        broadcast(this);
      });
    });
  };

  const originalBuildCharts = proto._buildCharts;
  proto._buildCharts = function () {
    originalBuildCharts.call(this);
    applyVisibility(this);
  };

  const originalRenderOverlays = proto._renderOverlays;
  proto._renderOverlays = function () {
    originalRenderOverlays.call(this);
    applyVisibility(this);
  };

  proto.setMovingAverageVisible = function (key, visible) {
    if (!VALID_KEYS.has(key) || this.mini) return false;
    ensureState(this);
    if (visible) this.enabledMovingAverages.add(key);
    else this.enabledMovingAverages.delete(key);
    saveEnabled(this.enabledMovingAverages);
    syncControls(this);
    applyVisibility(this);
    broadcast(this);
    return true;
  };

  proto.__movingAverageControlsInstalled = true;
  window.MovingAverageControls = {
    definitions: DEFINITIONS.slice(),
    applyVisibility,
  };
})();
