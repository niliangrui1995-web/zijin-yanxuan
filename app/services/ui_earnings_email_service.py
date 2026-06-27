# -*- coding: utf-8 -*-
"""Email digest helpers for the UI earnings-surprise workflow."""

from __future__ import annotations

import os
import smtplib
import ssl
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape
from pathlib import Path

from core.logger import get_logger
from domains.market_calendar import MarketCalendar

log = get_logger(__name__)

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
SMTP_TIMEOUT_SECONDS = 80
RECENT_EARNINGS_HOURS = 24
_SMTP_DELIVERY_ERRORS = (OSError, RuntimeError, TimeoutError, smtplib.SMTPException)


@dataclass(frozen=True)
class EarningsEmailConfig:
    sender_email: str
    sender_password: str
    receiver_email: str
    smtp_host: str = SMTP_HOST
    smtp_port: int = SMTP_PORT
    timeout_seconds: int = SMTP_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self.sender_email and self.sender_password and self.receiver_email)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_env_file_paths(environ: Mapping[str, str]) -> list[Path]:
    root = _project_root()
    paths = [
        root.parent / "每日战报" / "每日战报" / ".env",
        root / ".env",
    ]
    override = str(environ.get("VCP_EARNINGS_EMAIL_ENV_FILE") or "").strip()
    if override:
        paths.append(Path(override).expanduser())
    return paths


def _parse_env_line(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("export "):
        text = text[len("export ") :].strip()
    key, separator, value = text.partition("=")
    if not separator:
        return None
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return values
    for line in text.splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


def _parse_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def load_earnings_email_config(
    *,
    environ: Mapping[str, str] | None = None,
    env_file_paths: Iterable[Path | str] | None = None,
) -> EarningsEmailConfig:
    source_env = dict(os.environ if environ is None else environ)
    merged: dict[str, str] = {}
    paths = list(env_file_paths) if env_file_paths is not None else _default_env_file_paths(source_env)
    for raw_path in paths:
        merged.update(_load_env_file(Path(raw_path).expanduser()))
    merged.update(source_env)

    return EarningsEmailConfig(
        sender_email=str(merged.get("VCP_EARNINGS_EMAIL_ADDRESS") or merged.get("EMAIL_ADDRESS") or "").strip(),
        sender_password=str(merged.get("VCP_EARNINGS_EMAIL_PASSWORD") or merged.get("EMAIL_PASSWORD") or "").strip(),
        receiver_email=str(merged.get("VCP_EARNINGS_RECEIVER_EMAIL") or merged.get("RECEIVER_EMAIL") or "").strip(),
        smtp_host=str(merged.get("VCP_EARNINGS_SMTP_HOST") or SMTP_HOST).strip() or SMTP_HOST,
        smtp_port=_parse_positive_int(merged.get("VCP_EARNINGS_SMTP_PORT"), SMTP_PORT),
        timeout_seconds=_parse_positive_int(
            merged.get("VCP_EARNINGS_SMTP_TIMEOUT_SECONDS"),
            SMTP_TIMEOUT_SECONDS,
        ),
    )


def _split_receivers(receiver_email: str) -> list[str]:
    return [email.strip() for email in str(receiver_email or "").split(",") if email.strip()]


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _record_discovered_at(record: Mapping[str, object]) -> datetime | None:
    return (
        _parse_datetime(record.get("发现时间"))
        or _parse_datetime(record.get("揭晓日"))
        or _parse_datetime(record.get("discovered_at"))
    )


def select_recent_earnings_records(
    records: Iterable[Mapping[str, object]],
    *,
    now: datetime | None = None,
    hours: int = RECENT_EARNINGS_HOURS,
) -> list[dict[str, object]]:
    current = now or MarketCalendar.now("CN")
    cutoff = current - timedelta(hours=int(hours))
    selected: list[tuple[datetime, dict[str, object]]] = []
    for raw_record in records or []:
        record = dict(raw_record or {})
        discovered_at = _record_discovered_at(record)
        if discovered_at is None or discovered_at < cutoff or discovered_at > current:
            continue
        selected.append((discovered_at, record))
    selected.sort(key=lambda item: (item[0], _safe_float(item[1].get("环比增速_百分比"))), reverse=True)
    return [record for _discovered_at, record in selected]


def _is_blank(value: object) -> bool:
    return value is None or str(value).strip().lower() in {"", "nan", "nat", "none"}


def _safe_float(value: object) -> float:
    try:
        if _is_blank(value):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_percent(value: object) -> str:
    if _is_blank(value):
        return "--"
    return f"{_safe_float(value):.2f}%"


def _format_profit(value: object) -> str:
    amount = _safe_float(value)
    if not amount:
        return "--"
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:.2f}亿"
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:.2f}万"
    return f"{amount:.2f}"


def _format_text(value: object) -> str:
    return "--" if _is_blank(value) else str(value).strip()


def _build_rows_html(records: list[dict[str, object]]) -> str:
    rows: list[str] = []
    for record in records:
        cells = [
            _format_text(record.get("股票代码") or record.get("代码")),
            _format_text(record.get("股票名称") or record.get("名称")),
            _format_text(record.get("报告期")),
            _format_text(record.get("数据类型")),
            _format_text(record.get("基调")),
            _format_percent(record.get("环比增速_百分比")),
            _format_percent(record.get("同比增速_百分比")),
            _format_profit(record.get("单季净利润_新增")),
            _format_text(record.get("所属行业与概念")),
            _format_text(record.get("公告日期")),
            _format_text(record.get("源公告日期")),
            _format_text(record.get("发现时间") or record.get("揭晓日") or record.get("discovered_at")),
        ]
        rows.append("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in cells) + "</tr>")
    return "\n".join(rows)


def build_earnings_email_message(
    records: list[dict[str, object]],
    *,
    config: EarningsEmailConfig,
    now: datetime | None = None,
    hours: int = RECENT_EARNINGS_HOURS,
) -> MIMEText:
    current = now or MarketCalendar.now("CN")
    count = len(records)
    generated_at = current.strftime("%Y年%m月%d日 %H:%M")
    table_rows = _build_rows_html(records)
    html = f"""<html><head><meta charset="utf-8"><style>
body {{font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:#1f2933;background:#f5f7fb;margin:0;padding:20px;font-size:14px;line-height:1.7}}
.wrap {{max-width:1180px;margin:0 auto;background:#fff;border:1px solid #d8e0ea;border-radius:8px;overflow:hidden}}
.hdr {{background:#102a43;color:#fff;padding:18px 22px}}
.hdr h1 {{font-size:21px;margin:0 0 6px}}
.hdr p {{margin:0;color:#d9e2ec}}
.content {{padding:18px 22px}}
table {{border-collapse:collapse;width:100%;font-size:12px;background:#fff}}
th {{background:#edf2f7;color:#243b53;text-align:left;padding:8px;border-bottom:1px solid #cbd5e1;white-space:nowrap}}
td {{padding:8px;border-bottom:1px solid #e5eaf0;vertical-align:top}}
tr:nth-child(even) {{background:#f8fafc}}
.note {{color:#52606d;font-size:12px;margin-top:14px}}
</style></head><body><div class="wrap">
<div class="hdr"><h1>紫金研选业绩异动</h1><p>最近 {int(hours)} 小时新增 {count} 只 · {generated_at}</p></div>
<div class="content">
<table>
<thead><tr>
<th>代码</th><th>名称</th><th>报告期</th><th>类型</th><th>基调</th><th>环比</th><th>同比</th>
<th>单季净利润</th><th>行业与概念</th><th>公告日期</th><th>源公告日期</th><th>发现时间</th>
</tr></thead>
<tbody>{table_rows}</tbody>
</table>
<p class="note">自动发送，仅供研究参考，不构成投资建议。</p>
</div></div></body></html>"""
    message = MIMEText(html, "html", "utf-8")
    message["From"] = formataddr(("紫金研选", config.sender_email))
    message["To"] = config.receiver_email
    message["Subject"] = Header(f"【紫金研选业绩异动】24H新增 {count} 只 · {current.strftime('%m月%d日 %H:%M')}", "utf-8")
    return message


def _send_message(message: MIMEText, *, config: EarningsEmailConfig) -> None:
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        config.smtp_host,
        config.smtp_port,
        timeout=config.timeout_seconds,
        context=context,
    ) as smtp:
        smtp.set_debuglevel(0)
        smtp.login(config.sender_email, config.sender_password)
        smtp.sendmail(config.sender_email, _split_receivers(config.receiver_email), message.as_string())


def send_recent_earnings_email_digest(
    records: Iterable[Mapping[str, object]],
    *,
    now: datetime | None = None,
    hours: int = RECENT_EARNINGS_HOURS,
    config: EarningsEmailConfig | None = None,
) -> dict[str, object]:
    current = now or MarketCalendar.now("CN")
    recent_records = select_recent_earnings_records(records, now=current, hours=hours)
    if not recent_records:
        return {
            "job_key": "earnings_email_digest",
            "status": "skipped",
            "reason": "no_recent_records",
            "records": 0,
            "email_sent": False,
        }

    resolved_config = config or load_earnings_email_config()
    if not resolved_config.is_configured:
        log.warning("[业绩邮件] SMTP 未配置，跳过最近 24H 新增邮件发送")
        return {
            "job_key": "earnings_email_digest",
            "status": "skipped",
            "reason": "email_not_configured",
            "records": len(recent_records),
            "email_sent": False,
        }

    message = build_earnings_email_message(recent_records, config=resolved_config, now=current, hours=hours)
    try:
        _send_message(message, config=resolved_config)
    except _SMTP_DELIVERY_ERRORS as exc:
        log.error(f"[业绩邮件] 邮件发送失败: {exc}")
        return {
            "job_key": "earnings_email_digest",
            "status": "failed",
            "error": str(exc),
            "records": len(recent_records),
            "email_sent": False,
        }

    log.info(f"[业绩邮件] 已发送最近 {hours}H 新增业绩异动: {len(recent_records)} 条")
    return {
        "job_key": "earnings_email_digest",
        "status": "success",
        "records": len(recent_records),
        "email_sent": True,
    }
