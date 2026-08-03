/* moving_average_controls.js — MA/EMA主图均线总开关。
 *
 * 指标区只显示两个选项：
 * - MA：统一控制MA5/MA10/MA20/MA60
 * - EMA：统一控制EMA20/EMA60
 * 两组默认关闭，选择保存在浏览器本地。后台指标计算与技术分析不受影响。
 */
(function () {
  'use strict';

  const proto = window.KLineBoard && window.KLineBoard.prototype;
  if (!proto || proto.__movingAverageControlsInstalled) return;

  const STORAGE_KEY = 'ryan:kline:moving-average-groups:v2';
  const GROUPS = [
    { key: 'ma', label: 'MA', series: ['ma5', 'ma10', 'ma20', 'ma60'] },
    { key: 'ema', label: 'EMA', series: ['ema20', 'ema60'] },
  ];
  const VALID_GROUPS = new Set(GROUPS.map(group => group.key));

  function loadEnabled() {
    try {
      const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]');
      return Array.isArray(raw) ? raw.filter(key => VALID_GROUPS.has(key)) : [];
    } catch (_) {
      return [];
    }
  }

  function saveEnabled(enabled) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...enabled]));
    } catch (_) { /* 存储受限时只维持当前页面状态 */ }
  }

  function ensureState(board) {
    if (board.enabledMovingAverageGroups instanceof Set) return;
    board.enabledMovingAverageGroups = new Set(board.mini ? [] : loadEnabled());
  }

  function allSeries(board) {
    return Object.assign({}, board._maSeries || {}, board._emaSeries || {});
  }

  function applyVisibility(board) {
    ensureState(board);
    const series = allSeries(board);
    for (const group of GROUPS) {
      const visible = !board.mini && board.enabledMovingAverageGroups.has(group.key);
      for (const seriesKey of group.series) {
        if (series[seriesKey]) series[seriesKey].applyOptions({ visible });
      }
    }
  }

  function syncControls(board) {
    if (!board.el) return;
    board.el.querySelectorAll('[data-moving-average-group]').forEach(cb => {
      cb.checked = board.enabledMovingAverageGroups.has(cb.dataset.movingAverageGroup);
    });
  }

  function broadcast(source) {
    if (!document || !document.querySelectorAll) return;
    document.querySelectorAll('.kl-board').forEach(root => {
      const board = root.klBoard;
      if (!board || board === source || board.mini) return;
      board.enabledMovingAverageGroups = new Set(source.enabledMovingAverageGroups);
      syncControls(board);
      applyVisibility(board);
    });
  }

  const originalBuildDom = proto._buildDom;
  proto._buildDom = function () {
    ensureState(this);
    originalBuildDom.call(this);
    if (this.mini) return;

    const indicatorGroup = this.el.querySelector('.ind-group');
    if (!indicatorGroup || indicatorGroup.querySelector('[data-moving-average-group]')) return;

    const fragment = document.createDocumentFragment();
    const title = document.createElement('span');
    title.className = 'ma-control-title';
    title.textContent = '均线';
    fragment.appendChild(title);

    for (const group of GROUPS) {
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.movingAverageGroup = group.key;
      checkbox.checked = this.enabledMovingAverageGroups.has(group.key);
      label.appendChild(checkbox);
      label.appendChild(document.createTextNode(' ' + group.label));
      fragment.appendChild(label);
    }

    const divider = document.createElement('span');
    divider.className = 'ma-control-divider';
    divider.setAttribute('aria-hidden', 'true');
    fragment.appendChild(divider);
    indicatorGroup.insertBefore(fragment, indicatorGroup.firstChild);

    indicatorGroup.querySelectorAll('[data-moving-average-group]').forEach(cb => {
      cb.addEventListener('change', () => {
        const key = cb.dataset.movingAverageGroup;
        if (cb.checked) this.enabledMovingAverageGroups.add(key);
        else this.enabledMovingAverageGroups.delete(key);
        saveEnabled(this.enabledMovingAverageGroups);
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

  proto.setMovingAverageGroupVisible = function (groupKey, visible) {
    if (!VALID_GROUPS.has(groupKey) || this.mini) return false;
    ensureState(this);
    if (visible) this.enabledMovingAverageGroups.add(groupKey);
    else this.enabledMovingAverageGroups.delete(groupKey);
    saveEnabled(this.enabledMovingAverageGroups);
    syncControls(this);
    applyVisibility(this);
    broadcast(this);
    return true;
  };

  proto.__movingAverageControlsInstalled = true;
  window.MovingAverageControls = {
    groups: GROUPS.map(group => ({
      key: group.key,
      label: group.label,
      series: group.series.slice(),
    })),
    applyVisibility,
  };
})();
