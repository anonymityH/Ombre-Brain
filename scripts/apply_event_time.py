"""Apply the event-time implementation to the current checkout safely.

This exists because the connected GitHub write surface can replace whole files
but cannot apply a small textual patch.  Rewriting bucket_manager.py wholesale
would be much riskier than a guarded local transformation.

The script is intentionally fail-closed: every expected source fragment must
match exactly once in the 3.2.0 source before *any* file is written.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one source match, found {count}; "
            "no files were changed"
        )
    return text.replace(old, new, 1)


def _transform_import_memory(text: str) -> str:
    text = _replace_once(
        text,
        '''            name=item.get("name") or None,\n            source_tool="import",\n            event_actor="human",\n            imported=True,\n        )''',
        '''            name=item.get("name") or None,\n            source_tool="import",\n            event_actor="human",\n            imported=True,\n            event_time=item.get("event_time", ""),\n            event_time_end=item.get("event_time_end", ""),\n        )''',
        "import bucket forwarding",
    )

    text = _replace_once(
        text,
        '''        if not items:\n            return True\n\n        # --- 逐条保存提取出的记忆 ---''',
        '''        if not items:\n            return True\n\n        # Keep source chronology separate from the time OB creates the bucket.\n        # Conversation-export timestamps are trusted provenance; if a memory\n        # already carries a more specific event_time, keep it.  When the source\n        # has no timestamp, leave the field empty rather than inventing one.\n        source_event_time = str(chunk.get("timestamp_start") or "").strip()\n        source_event_time_end = str(chunk.get("timestamp_end") or "").strip()\n        for item in items:\n            if source_event_time and not item.get("event_time"):\n                item["event_time"] = source_event_time\n            if source_event_time_end and not item.get("event_time_end"):\n                item["event_time_end"] = source_event_time_end\n\n        # --- 逐条保存提取出的记忆 ---''',
        "chunk chronology fallback",
    )

    text = _replace_once(
        text,
        '''                "is_pattern": parse_bool(\n                    item.get("is_pattern", False), default=False\n                ),\n            })''',
        '''                "is_pattern": parse_bool(\n                    item.get("is_pattern", False), default=False\n                ),\n                "event_time": str(item.get("event_time") or "").strip(),\n                "event_time_end": str(item.get("event_time_end") or "").strip(),\n            })''',
        "extraction event-time fields",
    )
    return text


def _transform_bucket_manager(text: str) -> str:
    text = _replace_once(
        text,
        '''    "_pre_anchor_source_tool": _SOURCE_TOOL_MAX,\n}''',
        '''    "_pre_anchor_source_tool": _SOURCE_TOOL_MAX,\n    "event_time": 64,\n    "event_time_end": 64,\n}''',
        "event-time metadata limits",
    )

    text = _replace_once(
        text,
        '''        defer_derived_index: bool = False,\n        imported: bool = False,\n        source_refs: Any = None,''',
        '''        defer_derived_index: bool = False,\n        imported: bool = False,\n        event_time: str = "",\n        event_time_end: str = "",\n        source_refs: Any = None,''',
        "BucketManager.create signature",
    )

    text = _replace_once(
        text,
        '''        if imported:\n            metadata["imported"] = True\n        if test_data:''',
        '''        if imported:\n            metadata["imported"] = True\n        for field, value in (\n            ("event_time", event_time),\n            ("event_time_end", event_time_end),\n        ):\n            text_value = self._sanitize_text(str(value or "")).strip()[\n                :_METADATA_TEXT_LIMITS[field]\n            ]\n            if text_value:\n                metadata[field] = text_value\n        if test_data:''',
        "BucketManager.create metadata",
    )

    text = _replace_once(
        text,
        '''                  "source_tool", "grow_batch_id", "last_merged_by", "_pre_anchor_source_tool",\n                  # I 沉淀机制字段''',
        '''                  "source_tool", "grow_batch_id", "last_merged_by", "_pre_anchor_source_tool",\n                  # 历史事件时间与 OB 自身 created/last_active 分离。\n                  "event_time", "event_time_end",\n                  # I 沉淀机制字段''',
        "event-time update passthrough",
    )
    return text


def main() -> None:
    paths = {
        ROOT / "src" / "import_memory.py": _transform_import_memory,
        ROOT / "src" / "bucket_manager.py": _transform_bucket_manager,
    }

    # Transform everything in memory first.  A version/context mismatch aborts
    # before either source file is touched.
    transformed: dict[Path, str] = {}
    for path, transform in paths.items():
        original = path.read_text(encoding="utf-8")
        transformed[path] = transform(original)

    for path, content in transformed.items():
        path.write_text(content, encoding="utf-8", newline="\n")

    print("event-time source changes applied successfully")
    print("next: run pytest tests/test_import_event_time.py tests/test_import_extraction_json.py")


if __name__ == "__main__":
    main()
