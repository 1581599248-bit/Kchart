/* ema_overlay.js — EMA20/EMA60 常驻主图，不增加开关和额外请求。 */
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
        lastValueVisible: false, crosshairMarkerVisible: false,
      }),
      ema60: this.mainChart.addLineSeries({
        color: '#00bcd4', lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false,
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
