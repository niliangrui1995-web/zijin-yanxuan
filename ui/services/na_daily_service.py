import sys

import app.services.na_daily_service as _service

sys.modules[__name__] = _service
