/* ema_overlay.js — 提供EMA20/EMA60主图序列与数据。
 * 显示状态由moving_average_controls.js统一控制，默认关闭。
 */
(function () {
  'use strict';
  const proto = window.KLineBoard && window.KLineBoard.prototype;
  if (!proto) return;

  const originalBuild = proto._buildCharts;
  proto._buildCharts = function () {
    originalBuild.call(this);
    this._emaSeries = {
      ema20: this.mainChart.addLineSeries({
        color: '#ff8f3d', lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false, visible: false,
      }),
      ema60: this.mainChart.addLineSeries({
        color: '#00bcd4', lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false, visible: false,
      }),
    };
  };

  const originalRender = proto._renderOverlays;
  proto._renderOverlays = function () {
    originalRender.call(this);
    if (!this._emaSeries) return;
    this._emaSeries.ema20.setData(this.indCols.EMA20 || []);
    this._emaSeries.ema60.setData(this.indCols.EMA60 || []);
  };
})();
