"""v7 chart service adapter.

复用既有高速缓存、数据加载和序列化链路，只替换分析引擎并提升缓存版本。
"""
from . import analysis_v7
from . import chart_service as _base

_base.analysis_mod = analysis_v7
_base._BUNDLE_VERSION = "bundle_v7"

build = _base.build
get = _base.get
refresh_async = _base.refresh_async
refresh_many = _base.refresh_many
