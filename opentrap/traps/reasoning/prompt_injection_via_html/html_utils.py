from __future__ import annotations

import re


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            cleaned = "\n".join(lines[1:-1]).strip()
    return cleaned


def _is_minimally_valid_html(html: str) -> bool:
    lowered = html.lower()
    required = ("<!doctype html", "<html", "<head", "<body")
    return all(marker in lowered for marker in required)


def _replace_opening_tag(html: str, tag: str, new_tag: str) -> str:
    pattern = re.compile(rf"<{tag}([^>]*)>", flags=re.IGNORECASE)
    match = pattern.search(html)
    if match:
        start, end = match.span()
        return html[:start] + new_tag + html[end:]
    return html


def _insert_before_closing(html: str, closing_tag: str, payload: str) -> str:
    closing_lower = closing_tag.lower()
    lowered = html.lower()
    idx = lowered.find(closing_lower)
    if idx == -1:
        return html + payload
    return html[:idx] + payload + html[idx:]


def _insert_after_opening(html: str, opening_tag: str, payload: str) -> str:
    lowered = html.lower()
    idx = lowered.find(opening_tag.lower())
    if idx == -1:
        return payload + html
    insert_at = idx + len(opening_tag)
    return html[:insert_at] + payload + html[insert_at:]
