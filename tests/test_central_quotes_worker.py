# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QApplication, QWidget

from ui.workers.central_quotes_worker import CentralQuotesService


def test_central_quotes_service_uses_30s_a_share_polling():
    app = QApplication.instance() or QApplication([])
    main_window = QWidget()

    class DummyProvider:
        pass

    service = CentralQuotesService(main_window, DummyProvider())
    try:
        assert service._timer.interval() == 30000
        assert service._COOLDOWN_TICKS == 10
        assert service._heartbeat_every_ticks == 2
    finally:
        service.shutdown()
        service.deleteLater()
        main_window.deleteLater()
