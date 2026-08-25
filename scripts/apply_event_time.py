"""Apply the event-time implementation to the current checkout safely.

The connected GitHub write surface can replace whole files but cannot apply a
small textual patch. This local helper therefore performs guarded byte-level
replacements so every untouched byte in the source files remains untouched.

The script is fail-closed: every expected source fragment must match exactly
once, or the already-applied replacement must match exactly once, before any
file is written. It is safe to re-run after a successful application.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_NEWLINES = ("\r\n", "\n")


def _encoded_variant(text: str, newline: str) -> bytes:
    return text.replace("\n", newline).encode("utf-8")


def _replace_bytes_once(data: bytes, old: str, new: str, label: str) -> bytes:
    old_matches: list[tuple[str, bytes]] = []
    new_matches: list[tuple[str, bytes]] = []

    for newline in _NEWLINES:
        old_bytes = _encoded_variant(old, newline)
        new_bytes = _encoded_variant(new, newline)
        old_count = data.count(old_bytes)
        new_count = data.count(new_bytes)
        if old_count:
            old_matches.extend([(newline, old_bytes)] * old_count)
        if new_count:
            new_matches.extend([(newline, new_bytes)] * new_count)

    if len(old_matches) == 1 and not new_matches:
        newline, old_bytes = old_matches[0]
        new_bytes = _encoded_variant(new, newline)
        return data.replace(old_bytes, new_bytes, 1)

    if not old_matches and len(new_matches) == 1:
        return data

    raise RuntimeError(
        f"{label}: expected one old source match or one already-applied "
        f"replacement, found old={len(old_matches)}, new={len(new_matches)}; "
        "no files were changed"
    )


def _transform_import_memory(data: bytes) -> bytes:
    data = _replace_bytes_once(
        data,
        '''            name=item.get("name") or None,\n            source_tool="import",\n            event_actor="human",\n            imported=True,\n        )''',
        '''            name=item.get("name") or None,\n            source_tool="import",\n            event_actor="human",\n            imported=True,\n            event_time=item.get("event_time", ""),\n            event_time_end=item.get("event_time_end", ""),\n        )''',
        "import bucket forwarding",
    )

    data = _replace_bytes_once(
        data,
        '''        if not items:\n            return True\n\n        # --- 逐条保存提取出的记忆 ---''',
        '''        if not items:\n            return True\n\n        # Keep source chronology separate from the time OB creates the bucket.\n        # Conversation-export timestamps are trusted provenance; if a memory\n        # already carries a more specific event_time, keep it. When the source\n        # has no timestamp, leave the field empty rather than inventing one.\n        source_event_time = str(chunk.get("timestamp_start") or "").strip()\n        source_event_time_end = str(chunk.get("timestamp_end") or "").strip()\n        for item in items:\n            if source_event_time and not item.get("event_time"):\n                item["event_time"] = source_event_time\n            if source_event_time_end and not item.get("event_time_end"):\n                item["event_time_end"] = source_event_time_end\n\n        # --- 逐条保存提取出的记忆 ---''',
        "chunk chronology fallback",
    )

    data = _replace_bytes_once(
        data,
        '''                "is_pattern": parse_bool(\n                    item.get("is_pattern", False), default=False\n                ),\n            })''',
        '''                "is_pattern": parse_bool(\n                    item.get("is_pattern", False), default=False\n                ),\n                "event_time": str(item.get("event_time") or "").strip(),\n                "event_time_end": str(item.get("event_time_end") or "").strip(),\n            })''',
        "extraction event-time fields",
    )
    return data


def _transform_bucket_manager(data: bytes) -> bytes:
    data = _replace_bytes_once(
        data,
        '''    "_pre_anchor_source_tool": _SOURCE_TOOL_MAX,\n}''',
        '''    "_pre_anchor_source_tool": _SOURCE_TOOL_MAX,\n    "event_time": 64,\n    "event_time_end": 64,\n}''',
        "event-time metadata limits",
    )

    data = _replace_bytes_once(
        data,
        '''        defer_derived_index: bool = False,\n        imported: bool = False,\n        source_refs: Any = None,''',
        '''        defer_derived_index: bool = False,\n        imported: bool = False,\n        event_time: str = "",\n        event_time_end: str = "",\n        source_refs: Any = None,''',
        "BucketManager.create signature",
    )

    data = _replace_bytes_once(
        data,
        '''        if imported:\n            metadata["imported"] = True\n        if test_data:''',
        '''        if imported:\n            metadata["imported"] = True\n        for field, value in (\n            ("event_time", event_time),\n            ("event_time_end", event_time_end),\n        ):\n            text_value = self._sanitize_text(str(value or "")).strip()[\n                :_METADATA_TEXT_LIMITS[field]\n            ]\n            if text_value:\n                metadata[field] = text_value\n        if test_data:''',
        "BucketManager.create metadata",
    )

    data = _replace_bytes_once(
        data,
        '''                  "source_tool", "grow_batch_id", "last_merged_by", "_pre_anchor_source_tool",\n                  # I 沉淀机制字段''',
        '''                  "source_tool", "grow_batch_id", "last_merged_by", "_pre_anchor_source_tool",\n                  # 历史事件时间与 OB 自身 created/last_active 分离。\n                  "event_time", "event_time_end",\n                  # I 沉淀机制字段''',
        "event-time update passthrough",
    )
    return data


def main() -> None:
    paths = {
        ROOT / "src" / "import_memory.py": _transform_import_memory,
        ROOT / "src" / "bucket_manager.py": _transform_bucket_manager,
    }

    # Transform everything in memory first. A version/context mismatch aborts
    # before either source file is touched.
    transformed: dict[Path, bytes] = {}
    changed = False
    for path, transform in paths.items():
        original = path.read_bytes()
        updated = transform(original)
        transformed[path] = updated
        changed = changed or updated != original

    if changed:
        for path, content in transformed.items():
            path.write_bytes(content)
        print("event-time source changes applied successfully")
    else:
        print("event-time source changes already applied; no source files changed")

    print("next: run pytest tests/test_import_event_time.py tests/test_import_extraction_json.py")


if __name__ == "__main__":
    main()
