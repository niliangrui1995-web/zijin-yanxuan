# -*- coding: utf-8 -*-
from ui.main_window_network import update_network_ui


class _DummyAction:
    def __init__(self):
        self.text_value = ""

    def setText(self, text):
        self.text_value = text


class _DummyStatusDot:
    def __init__(self):
        self.color = None

    def set_color(self, color):
        self.color = color


class _DummyWindow:
    def __init__(self):
        self.act_network = _DummyAction()
        self.status_dot = _DummyStatusDot()


def test_update_network_ui_uses_readable_chinese_labels():
    window = _DummyWindow()

    update_network_ui(window, True)
    assert window.act_network.text_value == "网络状态：在线"
    assert window.status_dot.color == "#22C55E"

    update_network_ui(window, False)
    assert window.act_network.text_value == "网络状态：离线"
    assert window.status_dot.color == "#EF4444"
