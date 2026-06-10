"""Pure filter-state helpers for the fund holdings tab."""

from __future__ import annotations

from collections.abc import Callable, Iterable


def normalize_settings_values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def extract_subject_filter_options(rows: Iterable[dict]) -> list[str]:
    return list(
        dict.fromkeys(
            subject
            for row in rows
            if (subject := str(row.get("主体") or "").strip())
        )
    )


def extract_capital_attribute_filter_options(rows: Iterable[dict], options: Iterable[str]) -> list[str]:
    row_values = {str(row.get("_capital_attribute_value") or "").strip() for row in rows}
    return [option for option in options if option in row_values]


def format_change_filter_button_text(selected: Iterable[str], options: Iterable[str]) -> tuple[str, str]:
    option_list = list(options)
    option_rank = {label: index for index, label in enumerate(option_list)}
    ordered = sorted({item for item in selected if item in option_rank}, key=option_rank.__getitem__)
    if not ordered:
        return "变化：全部", "全部变化"
    if len(ordered) <= 2:
        text = f"变化：{' / '.join(ordered)}"
    else:
        text = f"变化：{len(ordered)}项"
    return text, "、".join(ordered)


def format_quarter_filter_button_text(latest_only: bool, selected_quarters: Iterable[str]) -> tuple[str, str]:
    selected = {str(item).strip() for item in selected_quarters if str(item).strip()}
    if latest_only:
        return "季度：最新", "仅显示各主体最新季度"
    if not selected:
        return "季度：全部", "显示全部季度"

    ordered = sorted(selected, reverse=True)
    if len(ordered) <= 2:
        text = f"季度：{' / '.join(ordered)}"
    else:
        text = f"季度：{len(ordered)}项"
    return text, "、".join(ordered)


def resolve_quarter_query_scope(
    latest_only: bool,
    selected_quarters: Iterable[str],
    *,
    latest_scope: str,
    all_scope: str,
    selected_scope: str,
) -> tuple[str, set[str]]:
    selected = {str(item).strip() for item in selected_quarters if str(item).strip()}
    if latest_only:
        return latest_scope, set()
    if selected:
        return selected_scope, selected
    return all_scope, set()


def quarter_scope_loaded(
    scope: str,
    quarter_keys: Iterable[str],
    *,
    loaded_scope: str,
    loaded_keys: Iterable[str],
    latest_scope: str,
    all_scope: str,
    selected_scope: str,
) -> bool:
    normalized_loaded_scope = str(loaded_scope or "").strip().lower()
    loaded_key_set = {str(item).strip() for item in loaded_keys if str(item).strip()}
    quarter_key_set = {str(item).strip() for item in quarter_keys if str(item).strip()}
    if normalized_loaded_scope == all_scope:
        return True
    if scope == latest_scope:
        return normalized_loaded_scope == latest_scope
    if scope == selected_scope:
        return bool(quarter_key_set) and quarter_key_set.issubset(loaded_key_set)
    return False


def build_current_filter_summary(
    *,
    subject_names: Iterable[str],
    capital_attributes: Iterable[str],
    capital_label: Callable[[str], str],
    latest_only: bool,
    selected_quarters: Iterable[str],
    change_types: Iterable[str],
    search_text: str,
) -> str:
    parts = []

    subject_text = " / ".join(sorted(str(item).strip() for item in subject_names if str(item).strip()))
    if subject_text:
        parts.append(subject_text)

    capital_text = " / ".join(
        capital_label(item)
        for item in sorted(str(value).strip() for value in capital_attributes if str(value).strip())
    )
    if capital_text:
        parts.append(capital_text)

    quarter_set = {str(item).strip() for item in selected_quarters if str(item).strip()}
    if latest_only:
        parts.append("最新季度")
    elif quarter_set:
        parts.append(" / ".join(sorted(quarter_set, reverse=True)))

    change_text = " / ".join(sorted(str(item).strip() for item in change_types if str(item).strip()))
    if change_text:
        parts.append(change_text)

    normalized_search_text = str(search_text or "").strip()
    if normalized_search_text:
        parts.append(normalized_search_text)

    return "｜".join(parts) if parts else "全部"
