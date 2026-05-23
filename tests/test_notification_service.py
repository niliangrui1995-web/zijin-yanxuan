import importlib


def test_windows_toast_passes_user_text_as_process_arguments(monkeypatch):
    notification_service = importlib.import_module("ui.components.notification_service")
    calls = []

    monkeypatch.setattr(notification_service.os, "name", "nt", raising=False)
    monkeypatch.setattr(notification_service, "windows_no_window_kwargs", lambda: {"creationflags": 0})
    monkeypatch.setattr(
        notification_service,
        "run_process",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )

    title = "x'; Start-Process calc; '"
    message = "line1'; Remove-Item C:\\important; '"

    notification_service._send_windows_toast(title, message)

    assert len(calls) == 1
    args, kwargs = calls[0]
    script = args[4]
    assert args[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    assert args[-2:] == [title, message]
    assert title not in script
    assert message not in script
    assert "$template.CreateTextNode($Title)" in script
    assert "$template.CreateTextNode($Message)" in script
    assert kwargs["check"] is False
