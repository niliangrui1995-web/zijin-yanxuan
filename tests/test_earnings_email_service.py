# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
from pathlib import Path

from app.services import ui_earnings_email_service as email_service
from app.services.ui_earnings_email_service import (
    EarningsEmailConfig,
    load_earnings_email_config,
    select_recent_earnings_records,
    send_recent_earnings_email_digest,
)


def test_select_recent_earnings_records_uses_discovery_time_not_announcement_date():
    now = dt.datetime(2026, 4, 20, 9, 0)
    records = [
        {
            "股票代码": "300308",
            "公告日期": "2026-04-20",
            "发现时间": "2026-04-20T08:30:00",
            "环比增速_百分比": 80.0,
        },
        {
            "股票代码": "600000",
            "公告日期": "2026-04-20",
            "揭晓日": "2026-04-20T08:45:00",
            "环比增速_百分比": 120.0,
        },
        {
            "股票代码": "000001",
            "公告日期": "2026-04-20",
            "发现时间": "2026-04-19T08:59:59",
            "环比增速_百分比": 200.0,
        },
        {
            "股票代码": "002001",
            "公告日期": "2026-04-01",
            "发现时间": "2026-04-20T07:59:59",
            "环比增速_百分比": 50.0,
        },
    ]

    selected = select_recent_earnings_records(records, now=now)

    assert [record["股票代码"] for record in selected] == ["600000", "300308", "002001"]


def test_send_recent_earnings_digest_skips_empty_recent_window(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, "_send_message", lambda message, *, config: sent.append(message))

    result = send_recent_earnings_email_digest(
        [{"股票代码": "300308", "公告日期": "2026-04-20"}],
        now=dt.datetime(2026, 4, 20, 9, 0),
        config=EarningsEmailConfig("sender@example.com", "secret", "receiver@example.com"),
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "no_recent_records"
    assert result["email_sent"] is False
    assert sent == []


def test_send_recent_earnings_digest_sends_when_discovery_time_is_recent(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, "_send_message", lambda message, *, config: sent.append((message, config)))

    result = send_recent_earnings_email_digest(
        [
            {
                "股票代码": "300308",
                "股票名称": "中际旭创",
                "发现时间": "2026-04-20T08:30:00",
                "环比增速_百分比": 57.69,
            }
        ],
        now=dt.datetime(2026, 4, 20, 9, 0),
        config=EarningsEmailConfig("sender@example.com", "secret", "receiver@example.com"),
    )

    assert result["status"] == "success"
    assert result["records"] == 1
    assert result["email_sent"] is True
    assert len(sent) == 1
    html = sent[0][0].get_payload(decode=True).decode("utf-8")
    assert "300308" in html


def test_load_earnings_email_config_reuses_daily_report_env_path(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "EMAIL_ADDRESS=sender@example.com",
                "EMAIL_PASSWORD=secret",
                "RECEIVER_EMAIL=receiver1@example.com,receiver2@example.com",
                "VCP_EARNINGS_SMTP_PORT=bad",
            ]
        ),
        encoding="utf-8",
    )

    config = load_earnings_email_config(environ={}, env_file_paths=[env_file])

    assert config.sender_email == "sender@example.com"
    assert config.sender_password == "secret"
    assert config.receiver_email == "receiver1@example.com,receiver2@example.com"
    assert config.smtp_port == email_service.SMTP_PORT
