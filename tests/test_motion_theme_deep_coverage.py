from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QMenu, QPushButton

from ui import theme as theme_module
from ui.components import motion


class _Signal:
    def __init__(self):
        self.slots = []
        self.emitted = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        self.emitted.append(args)
        for slot in tuple(self.slots):
            slot(*args)


class _Effect:
    def __init__(self, parent):
        self.parent = parent
        self.opacity = None

    def setOpacity(self, value):
        self.opacity = value


class _Animation:
    def __init__(self, effect, property_name, parent):
        self.effect = effect
        self.property_name = property_name
        self.parent = parent
        self.finished = _Signal()
        self.started = False

    def setDuration(self, value):
        self.duration = value

    def setStartValue(self, value):
        self.start_value = value

    def setEndValue(self, value):
        self.end_value = value

    def setEasingCurve(self, value):
        self.easing = value

    def start(self):
        self.started = True


class _Widget:
    def __init__(self, *, visible=True, width=20, height=10):
        self.visible = visible
        self._width = width
        self._height = height
        self.effect = None

    def isVisible(self):
        return self.visible

    def width(self):
        return self._width

    def height(self):
        return self._height

    def setGraphicsEffect(self, effect):
        self.effect = effect

    def graphicsEffect(self):
        return self.effect


def test_motion_duration_and_fade_in_guards(monkeypatch):
    monkeypatch.setattr(motion, "build_ui_tokens", lambda: {"motion": {"fast": "75"}})
    assert motion.motion_duration("fast") == 75
    assert motion.motion_duration("missing", 33) == 33
    monkeypatch.setattr(motion, "build_ui_tokens", lambda: {"motion": {"fast": object()}})
    assert motion.motion_duration("fast", 44) == 44

    assert motion.fade_in(None) is None
    assert motion.fade_in(_Widget(visible=False)) is None
    assert motion.fade_in(_Widget(width=0)) is None

    monkeypatch.setattr(motion, "QGraphicsOpacityEffect", _Effect)
    monkeypatch.setattr(motion, "QPropertyAnimation", _Animation)
    monkeypatch.setattr(motion, "motion_duration", lambda *_args, **_kwargs: 90)
    widget = _Widget()
    animation = motion.fade_in(widget, start=0.25, end=0.9)

    assert animation.started is True
    assert animation.duration == 90
    assert animation.start_value == 0.25
    assert animation.end_value == 0.9
    assert widget.effect.opacity == 0.25
    animation.finished.emit()
    assert widget.effect is None


def test_motion_installers_are_idempotent(monkeypatch, qt_application):
    calls = []
    monkeypatch.setattr(motion, "fade_in", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(motion, "motion_duration", lambda *_args, **_kwargs: 60)

    button = QPushButton("go")
    event_filter = motion.ButtonFeedbackFilter(button)
    press = QEvent(QEvent.Type.MouseButtonPress)
    assert event_filter.eventFilter(button, press) is False
    button.setEnabled(False)
    assert event_filter.eventFilter(button, press) is False
    assert len(calls) == 1

    assert motion.install_button_feedback(None) is None
    motion.install_button_feedback(button)
    installed = button._motion_feedback_filter
    motion.install_button_feedback(button)
    assert button._motion_feedback_filter is installed

    monkeypatch.setattr(motion.QTimer, "singleShot", lambda _delay, callback: callback())
    menu = QMenu()
    assert motion.install_menu_fade(None) is None
    motion.install_menu_fade(menu)
    menu.aboutToShow.emit()
    call_count = len(calls)
    motion.install_menu_fade(menu)
    menu.aboutToShow.emit()
    assert len(calls) == call_count + 1
    menu.deleteLater()
    button.deleteLater()


class _Settings:
    def __init__(self):
        self.values = {}
        self.sync_calls = 0

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        self.sync_calls += 1


class _Timer:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


def test_theme_manager_method_branches(monkeypatch):
    signal = _Signal()
    settings = _Settings()
    timer = _Timer()
    auto_calls = []
    manager = SimpleNamespace(
        THEMES={
            "dark": {"appearance": "dark", "token": "D"},
            "light": {"appearance": "light", "token": "L"},
            "custom": {"appearance": "dark", "token": "C"},
        },
        _current_name="dark",
        _auto_switch=False,
        _settings=settings,
        _auto_timer=timer,
        sig_theme_changed=signal,
        set_auto_switch=lambda enabled: auto_calls.append(enabled),
        current_theme={"appearance": "dark", "token": "D"},
    )

    assert theme_module.ThemeManager.current_theme_name.fget(manager) == "dark"
    assert theme_module.ThemeManager.current_theme.fget(manager)["token"] == "D"
    assert theme_module.ThemeManager.get(manager, "missing") == ""
    assert theme_module.ThemeManager.is_dark(manager) is True
    assert theme_module.ThemeManager.theme_names(manager) == ["dark", "light", "custom"]
    assert theme_module.ThemeManager.is_auto_switch(manager) is False

    assert theme_module.ThemeManager.switch_theme(manager, "missing") is None
    assert theme_module.ThemeManager.switch_theme(manager, "dark") is None
    manager._auto_switch = True
    theme_module.ThemeManager.switch_theme(manager, "custom")
    assert auto_calls == [False]
    assert manager._current_name == "custom"
    assert settings.values["current_theme"] == "custom"
    assert signal.emitted == [("custom",)]

    checks = []
    manager._check_auto_switch = lambda: checks.append("check")
    theme_module.ThemeManager.set_auto_switch(manager, True)
    assert timer.started and checks == ["check"]
    theme_module.ThemeManager.set_auto_switch(manager, False)
    assert not timer.started

    manager._auto_switch = False
    assert theme_module.ThemeManager._check_auto_switch(manager) is None

    class _Morning:
        @staticmethod
        def now():
            return SimpleNamespace(hour=9)

    switched = []
    manager._auto_switch = True
    manager._current_name = "dark"
    manager.switch_theme = switched.append
    monkeypatch.setattr(theme_module, "_datetime", _Morning)
    theme_module.ThemeManager._check_auto_switch(manager)
    assert switched


def test_theme_alias_defaults_cover_light_and_existing_tokens():
    light = theme_module._with_alias_tokens(
        {
            "appearance": "light",
            "BG_CANVAS": "canvas",
            "BG_CARD": "card",
            "BG_ELEVATED": "raised",
            "BRAND_PRIMARY": "brand",
            "BRAND_HOVER": "hover",
            "BRAND_DEEP": "deep",
            "BRAND_SUBTLE": "subtle",
            "TEXT_PRIMARY": "text",
            "COLOR_SUCCESS": "success",
            "COLOR_ERROR": "error",
            "COLOR_WARNING": "warning",
            "SELECTION_BG": "selection",
            "SELECTION_HOVER_BG": "selection-hover",
            "BORDER_SUBTLE": "border",
        }
    )
    assert light["BG_TOOLBAR"] == "card"
    assert light["PRIMARY_GRADIENT_END"] == "deep"
    assert light["NETWORK_ONLINE"] == "success"

    dark = dict(light)
    dark["appearance"] = "dark"
    dark.pop("BG_TOOLBAR")
    dark.pop("TAB_ACTIVE_INDICATOR")
    dark["TAB_ACTIVE_TOP"] = "existing"
    enriched = theme_module._with_alias_tokens(dark)
    assert enriched["BG_TOOLBAR"] == "raised"
    assert enriched["TAB_ACTIVE_INDICATOR"] == "existing"
