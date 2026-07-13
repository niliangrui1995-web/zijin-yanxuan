import sys

from infra.market_data import tdx_data_provider as _provider_module

sys.modules[__name__] = _provider_module
