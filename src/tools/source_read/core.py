"""Read immutable source evidence behind one precisely identified bucket."""

from __future__ import annotations

import asyncio
import unicodedata
from typing import Any

from ombrebrain.storage.source_store import source_links_from_metadata
from utils import count_tokens_approx, normalize_memory_title

from .. import _runtime as rt
from ..plan.core import is_letter_bucket, letter_lock_state


_DEFAULT_MAX_TOKENS = 6000
_MAX_MAX_TOKENS = 20000
# The token estimator counts every character as at least 0.05 token. This
# keeps the bounded page window below roughly 400 KiB at the public maximum.
_MAX_CHARS_PER_TOKEN = 20


def _normalized_title(value: object) -> str:
    try:
        return normalize_memory_title(value)
    except ValueError:
        # Compatibility for buckets created before title limits existed.
        return " ".join(unicodedata.normalize("NFC", str(value or "")).split())


def _single_line_header_value(value: object) -> str:
    """Prevent control characters in historical data from forging headers."""

    output: list[str] = []
    for char in _normalized_title(value):
        if char == "\\":
            output.append("\\\\")
        elif unicodedata.category(char).startswith("C"):
            output.append(f"\\u{ord(char):04x}")
        else:
            output.append(char)
    return "".join(output)


def _collect_evidence_window(
    source_store: Any,
    source_refs: list[dict[str, Any]],
    *,
    scope: str,
    cursor: int,
    capture_limit: int,
) -> tuple[str, int]:
    """Read one source at a time and retain only the requested page window."""

    page_start = cursor
    page_end = cursor + capture_limit
    page_parts: list[str] = []
    total_chars = 0
    emitted_chunks = 0
    seen_full_sources: set[str] = set()

    def append_segment(segment: str) -> None:
        nonlocal total_chars
        segment_start = total_chars
        total_chars += len(segment)
        overlap_start = max(page_start, segment_start)
        overlap_end = min(page_end, total_chars)
        if overlap_start < overlap_end:
            page_parts.append(
                segment[overlap_start - segment_start : overlap_end - segment_start]
            )

    for source_ref in source_refs:
        ref = source_ref["ref"]
        if scope == "full_source":
            if ref in seen_full_sources:
                continue
            seen_full_sources.add(ref)
        elif not source_ref["ranges"]:
            # Empty ranges mean no event excerpt was declared. They never
            # silently widen an event read into the whole source.
            continue

        content = source_store.read(ref)
        if scope == "event":
            content = source_store.select_ranges(content, source_ref["ranges"])
        if emitted_chunks:
            append_segment("\n\n")
        append_segment(content)
        emitted_chunks += 1

    return "".join(page_parts), total_chars


def _render_page(
    *,
    bucket_id: str,
    title: str,
    metadata: dict[str, Any],
    source_refs: list[dict[str, Any]],
    scope: str,
    cursor: int,
    body: str,
    total_chars: int,
) -> str:
    end = cursor + len(body)
    next_cursor = end if end < total_chars else 0
    refs = ",".join(item["ref"] for item in source_refs)
    hashes = ",".join(item["ref"].removeprefix("src_") for item in source_refs)
    range_manifest = ";".join(
        ",".join(f"{start}-{finish}" for start, finish in item["ranges"]) or "none"
        for item in source_refs
    )
    header = (
        f"bucket_id={_single_line_header_value(bucket_id)}\n"
        f"title={_single_line_header_value(title)}\n"
        f"scope={scope}\n"
        "untrusted_source=true\n"
        f"source_refs={refs}\n"
        f"source_sha256={hashes}\n"
        f"source_ranges={range_manifest}\n"
        f"event_time={_single_line_header_value(metadata.get('event_time') or '')}\n"
        f"event_time_end={_single_line_header_value(metadata.get('event_time_end') or '')}\n"
        f"cursor={cursor}\n"
        f"next_cursor={next_cursor}\n"
        f"total_chars={total_chars}\n"
    )
    return header + "\n" + body


async def dispatch(
    bucket_id: str,
    expected_title: str,
    scope: str = "event",
    cursor: int = 0,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    source_slots: list[int] | None = None,
    all_sources: bool = False,
) -> str:
    bucket_id = str(bucket_id or "").strip()
    expected_title = _normalized_title(expected_title)
    scope = str(scope or "event").strip().lower()
    if not bucket_id or not expected_title:
        return "source_read 需要 bucket_id 和 expected_title。"
    if scope not in {"event", "full_source"}:
        return "scope 仅支持 event 或 full_source。"
    try:
        cursor = max(0, int(cursor))
        max_tokens = max(200, min(_MAX_MAX_TOKENS, int(max_tokens)))
    except (TypeError, ValueError, OverflowError):
        return "cursor 和 max_tokens 必须是整数。"

    getter = getattr(rt.bucket_mgr, "get_including_archive", rt.bucket_mgr.get)
    bucket = await getter(bucket_id)
    if not bucket:
        return f"未找到桶 {bucket_id}。"
    if is_letter_bucket(bucket) and letter_lock_state(bucket, "ai")["locked"]:
        return "这封信尚未向你开放，拒绝读取其原文证据。"
    metadata = bucket.get("metadata") or {}
    actual_title = _normalized_title(metadata.get("title"))
    if not actual_title:
        return "该桶没有可供精确校验的显式标题，拒绝读取原文。"
    if expected_title != actual_title:
        return "标题不匹配，拒绝读取原文。请使用该桶的精确 title。"

    try:
        links = source_links_from_metadata(metadata)
    except ValueError:
        return "该桶的原文证据引用格式无效，拒绝读取。"
    if not links:
        return "该桶没有原文证据引用。"
    if source_slots is not None and all_sources:
        return "source_slots 与 all_sources 不能同时使用。"
    if source_slots is None and not all_sources and (
        len(links) > 1 or any(link["status"] != "active" for link in links)
    ):
        return "source manifest\n" + "\n".join(
            f"slot={index} | ref={link['ref']} | ranges="
            + ",".join(f"{start}-{finish}" for start, finish in link["ranges"])
            + f" | {link['status']}"
            for index, link in enumerate(links, 1)
        )
    if source_slots is not None:
        if not isinstance(source_slots, list) or any(
            isinstance(slot, bool) or not isinstance(slot, int)
            for slot in source_slots
        ):
            return "source_slots 必须是整数列表。"
        selected: list[dict[str, Any]] = []
        for slot in sorted(set(source_slots)):
            if slot < 1 or slot > len(links):
                return f"source_slot={slot} 不存在。"
            link = links[slot - 1]
            if link["status"] != "active":
                return f"source_slot={slot} 已 detached，拒绝读取。"
            selected.append(link)
        source_refs = selected
    else:
        source_refs = [link for link in links if link["status"] == "active"]
    if not source_refs:
        return "该桶没有活动的原文证据引用。"
    if scope == "event" and not any(item["ranges"] for item in source_refs):
        return (
            "该桶未声明事件原文范围，拒绝将整份原文作为事件返回。"
            "如确需整份原文，请显式使用 scope=full_source。"
        )

    try:
        evidence_window, total_chars = await asyncio.to_thread(
            _collect_evidence_window,
            rt.source_store,
            source_refs,
            scope=scope,
            cursor=cursor,
            capture_limit=max_tokens * _MAX_CHARS_PER_TOKEN,
        )
    except (OSError, UnicodeError, ValueError):
        return "原文证据读取或完整性校验失败。"
    if cursor >= total_chars:
        return f"原文已读完（cursor={cursor}，总字符 {total_chars}）。"

    low, high = 0, len(evidence_window)
    chosen = 0
    while low <= high:
        middle = (low + high) // 2
        candidate = _render_page(
            bucket_id=bucket_id,
            title=actual_title,
            metadata=metadata,
            source_refs=source_refs,
            scope=scope,
            cursor=cursor,
            body=evidence_window[:middle],
            total_chars=total_chars,
        )
        if count_tokens_approx(candidate) <= max_tokens:
            chosen = middle
            low = middle + 1
        else:
            high = middle - 1

    if chosen == 0:
        return "max_tokens 不足以容纳原文分页头，请提高后重试。"
    return _render_page(
        bucket_id=bucket_id,
        title=actual_title,
        metadata=metadata,
        source_refs=source_refs,
        scope=scope,
        cursor=cursor,
        body=evidence_window[:chosen],
        total_chars=total_chars,
    )
