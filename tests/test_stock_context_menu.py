# -*- coding: utf-8 -*-
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QUrl

from ui.components import stock_context_menu
from ui.components.stock_context_menu import build_codex_project_thread_url


def test_codex_project_thread_url_targets_industry_research_project():
    parsed = urlparse(build_codex_project_thread_url())

    assert parsed.scheme == "codex"
    assert parsed.netloc == "threads"
    assert parsed.path == "/new"
    assert parse_qs(parsed.query)["path"] == [r"D:\vcp_hunter\产业链投研"]


def test_codex_project_thread_url_is_valid_qurl():
    qurl = QUrl(build_codex_project_thread_url())

    assert qurl.isValid()
    assert qurl.scheme() == "codex"
    assert qurl.host() == "threads"
    assert qurl.path() == "/new"
    assert "path=" in qurl.query()


def test_open_codex_project_thread_uses_qdesktopservices(monkeypatch, tmp_path):
    opened_urls = []

    class FakeDesktopServices:
        @staticmethod
        def openUrl(qurl):
            opened_urls.append(qurl)
            return True

    monkeypatch.setattr(stock_context_menu, "QDesktopServices", FakeDesktopServices)
    monkeypatch.setattr(stock_context_menu, "_is_codex_scheme_registered", lambda: True)
    monkeypatch.setattr(
        stock_context_menu.webbrowser,
        "open_new_tab",
        lambda _url: (_ for _ in ()).throw(AssertionError("fallback should not run")),
    )

    assert stock_context_menu.open_codex_project_thread(project_path=tmp_path)
    assert opened_urls[0].scheme() == "codex"
    assert opened_urls[0].host() == "threads"
    assert opened_urls[0].path() == "/new"


def test_open_codex_project_thread_falls_back_to_webbrowser(monkeypatch, tmp_path):
    fallback_urls = []

    class FakeDesktopServices:
        @staticmethod
        def openUrl(_qurl):
            return False

    monkeypatch.setattr(stock_context_menu, "QDesktopServices", FakeDesktopServices)
    monkeypatch.setattr(stock_context_menu, "_is_codex_scheme_registered", lambda: True)
    monkeypatch.setattr(stock_context_menu.webbrowser, "open_new_tab", lambda url: fallback_urls.append(url) or True)

    assert stock_context_menu.open_codex_project_thread(project_path=tmp_path)
    assert fallback_urls == [build_codex_project_thread_url(tmp_path)]


def test_open_codex_project_thread_warns_when_project_path_is_missing(monkeypatch, tmp_path):
    warnings = []
    missing_path = tmp_path / "missing-project"

    class FakeDesktopServices:
        @staticmethod
        def openUrl(_qurl):
            raise AssertionError("desktop opener should not run")

    monkeypatch.setattr(stock_context_menu, "QDesktopServices", FakeDesktopServices)
    monkeypatch.setattr(stock_context_menu, "_warn_codex_open_failed", lambda _parent, message: warnings.append(message))

    assert not stock_context_menu.open_codex_project_thread(project_path=missing_path)
    assert "项目路径不存在" in warnings[0]


def test_open_codex_project_thread_warns_when_codex_scheme_is_missing(monkeypatch, tmp_path):
    warnings = []

    class FakeDesktopServices:
        @staticmethod
        def openUrl(_qurl):
            raise AssertionError("desktop opener should not run")

    monkeypatch.setattr(stock_context_menu, "QDesktopServices", FakeDesktopServices)
    monkeypatch.setattr(stock_context_menu, "_is_codex_scheme_registered", lambda: False)
    monkeypatch.setattr(stock_context_menu, "_warn_codex_open_failed", lambda _parent, message: warnings.append(message))

    assert not stock_context_menu.open_codex_project_thread(project_path=tmp_path)
    assert "没有注册 codex:// 深链接" in warnings[0]
