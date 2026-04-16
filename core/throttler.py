from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot


class SignalThrottler(QObject):
    """
    通用 PyQt 信号节流器 / 防抖器 (Throttler/Debouncer)。
    用于高频刷新的场景拦截，使得 UI 最多只会以 fixed interval (如 1000 毫秒) 统一渲染一次，
    丢弃掉中间极其密集的重复刷新请求，保护主 UI 线程不卡死。
    """
    # 当节流周期结束后，抛出合并后的最后一次数据
    throttled_signal = pyqtSignal(object)

    def __init__(self, interval=500, parent=None):
        super().__init__(parent)
        self.interval = interval
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._process_queue)
        self.pending_args = None

    @pyqtSlot(object)
    def trigger(self, args):
        """
        外源高频信号调用本方法。将数据暂存在 pending_args 中。
        如果定时器没在跑，就启动一轮。
        如果定时器在跑，说明处于冷却期，只覆盖最新数据，不立即刷新 UI。
        """
        self.pending_args = args
        if not self.timer.isActive():
            self.timer.start(self.interval)

    def _process_queue(self):
        """冷却结束，派发最新的那一波数据"""
        if self.pending_args is not None:
            self.throttled_signal.emit(self.pending_args)
            self.pending_args = None
