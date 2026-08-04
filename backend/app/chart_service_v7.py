"""Chart service adapter for the high-recall M/W geometry engine."""
from . import analysis_v7
from . import chart_service as _base

_base.analysis_mod = analysis_v7
_base._BUNDLE_VERSION = "bundle_v15_mw_recall"

build = _base.build
get = _base.get
refresh_async = _base.refresh_async
refresh_many = _base.refresh_many
