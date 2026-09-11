"""Regression tests for preserving source chronology during history imports."""

import pytest

from bucket_manager import BucketManager
from import_memory import (
    ImportEngine,
    _timestamp_text,
    chunk_turns,
    detect_and_parse,
)


class _FakeBucketManager:
    def __init__(self):
        self.created = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return "event-time-test-bucket"


class _FakeDehydrator:
    api_available = True


def _engine(tmp_path):
    manager = _FakeBucketManager()
    engine = ImportEngine(
        {"buckets_dir": str(tmp_path), "human": "测试用户"},
        manager,
        _FakeDehydrator(),
    )
    return engine, manager


def test_unix_export_timestamp_is_stored_as_explicit_utc():
    assert _timestamp_text(0) == "1970-01-01T00:00:00+00:00"


def test_browser_plugin_markdown_preserves_roles_and_message_times():
    raw = """> From: https://chatgpt.com/c/example

GPT:

没有时间的开场回复。

---

User:

message time: 2026-09-11 16:19:23

第一条用户消息。

---

GPT:

第一条回复。

---

User:

message time: 2026-09-11 16:21:08

第二条用户消息。

---

GPT:

第二条回复。
"""

    turns = detect_and_parse(raw, "browser-export.md")

    assert [turn["role"] for turn in turns] == [
        "assistant", "user", "assistant", "user", "assistant"
    ]
    assert [turn["timestamp"] for turn in turns] == [
        "", "2026-09-11 16:19:23", "", "2026-09-11 16:21:08", ""
    ]
    assert turns[0]["content"] == "没有时间的开场回复。"
    assert turns[-1]["content"] == "第二条回复。"
    assert all("message time:" not in turn["content"] for turn in turns)
    assert all("> From:" not in turn["content"] for turn in turns)
    assert all("---" not in turn["content"] for turn in turns)

    chunks = chunk_turns(turns, human_label="测试用户")
    assert chunks[0]["timestamp_start"] == "2026-09-11 16:19:23"
    assert chunks[0]["timestamp_end"] == "2026-09-11 16:21:08"
    assert chunks[0]["turn_manifest"] == [
        {"turn": 1, "role": "assistant", "timestamp": "", "start_line": 1, "end_line": 1},
        {"turn": 2, "role": "user", "timestamp": "2026-09-11 16:19:23", "start_line": 2, "end_line": 2},
        {"turn": 3, "role": "assistant", "timestamp": "", "start_line": 3, "end_line": 3},
        {"turn": 4, "role": "user", "timestamp": "2026-09-11 16:21:08", "start_line": 4, "end_line": 4},
        {"turn": 5, "role": "assistant", "timestamp": "", "start_line": 5, "end_line": 5},
    ]


def test_parse_extraction_preserves_model_event_time_fields():
    raw = """[
      {
        "content": "测试用户在上午完成了时间线测试。",
        "event_time": "2026-08-22T10:00:00+08:00",
        "event_time_end": "2026-08-22T10:05:00+08:00"
      }
    ]"""

    item = ImportEngine._parse_extraction(raw)[0]

    assert item["event_time"] == "2026-08-22T10:00:00+08:00"
    assert item["event_time_end"] == "2026-08-22T10:05:00+08:00"


def test_parse_extraction_preserves_valid_source_turn_selection():
    raw = """[
      {
        "content": "测试用户在不同轮次确认了一件重要的事。",
        "source_turns": [5, 2, 5, 0, "3", true, "bad"]
      }
    ]"""

    item = ImportEngine._parse_extraction(raw)[0]

    assert item["source_turns"] == [2, 3, 5]


@pytest.mark.asyncio
async def test_selected_turns_narrow_source_ranges_and_event_time(
    tmp_path,
    monkeypatch,
):
    engine, manager = _engine(tmp_path)
    transcript = "\n".join([
        "[AI] 开场",
        "[测试用户] 第一件事",
        "第一件事的补充",
        "[AI] 第一件事的回应",
        "[测试用户] 第二件事",
        "[AI] 第二件事的回应",
    ])
    manifest = [
        {"turn": 1, "role": "assistant", "timestamp": "", "start_line": 1, "end_line": 1},
        {"turn": 2, "role": "user", "timestamp": "2026-08-22T10:00:00+08:00", "start_line": 2, "end_line": 3},
        {"turn": 3, "role": "assistant", "timestamp": "", "start_line": 4, "end_line": 4},
        {"turn": 4, "role": "user", "timestamp": "2026-08-22T10:20:00+08:00", "start_line": 5, "end_line": 5},
        {"turn": 5, "role": "assistant", "timestamp": "", "start_line": 6, "end_line": 6},
    ]

    async def fake_extract(content, **kwargs):
        assert content == transcript
        assert kwargs["turn_manifest"] == manifest
        return [
            {"content": "第一件事形成了独立记忆。", "source_turns": [2, 3]},
            {"content": "第二件事形成了另一条记忆。", "source_turns": [4, 5]},
        ]

    monkeypatch.setattr(engine, "_extract_memories", fake_extract)
    monkeypatch.setattr(
        engine,
        "_create_import_item_if_new",
        engine._create_import_bucket,
    )

    ok = await engine._process_single_chunk(
        {
            "content": transcript,
            "timestamp_start": "2026-08-22T10:00:00+08:00",
            "timestamp_end": "2026-08-22T10:20:00+08:00",
            "turn_count": 5,
            "turn_manifest": manifest,
        },
        preserve_raw=False,
    )

    assert ok is True
    first, second = manager.created
    assert first["source_refs"][0]["ranges"] == [[2, 4]]
    assert first["event_time"] == "2026-08-22T10:00:00+08:00"
    assert first["event_time_end"] == "2026-08-22T10:00:00+08:00"
    assert second["source_refs"][0]["ranges"] == [[5, 6]]
    assert second["event_time"] == "2026-08-22T10:20:00+08:00"
    assert second["event_time_end"] == "2026-08-22T10:20:00+08:00"
    assert first["source_refs"][0]["ref"] == second["source_refs"][0]["ref"]


@pytest.mark.asyncio
async def test_chunk_source_range_fills_missing_event_time(tmp_path, monkeypatch):
    engine, manager = _engine(tmp_path)

    async def fake_extract(content, **kwargs):
        assert "测试用户" in content
        return [{"content": "测试用户完成了时间线测试。"}]

    monkeypatch.setattr(engine, "_extract_memories", fake_extract)
    monkeypatch.setattr(engine, "_create_import_item_if_new", engine._create_import_bucket)

    ok = await engine._process_single_chunk(
        {
            "content": "[测试用户] 我完成时间线测试啦",
            "timestamp_start": "2026-08-22T10:00:00+08:00",
            "timestamp_end": "2026-08-22T10:05:00+08:00",
            "turn_count": 2,
        },
        preserve_raw=False,
    )

    assert ok is True
    assert manager.created[0]["event_time"] == "2026-08-22T10:00:00+08:00"
    assert manager.created[0]["event_time_end"] == "2026-08-22T10:05:00+08:00"


@pytest.mark.asyncio
async def test_import_bucket_forwards_event_time_without_reusing_created_time(tmp_path):
    engine, manager = _engine(tmp_path)

    await engine._create_import_bucket(
        {
            "content": "一条历史记忆",
            "event_time": "2026-08-20T09:30:00+08:00",
            "event_time_end": "2026-08-20T09:45:00+08:00",
        }
    )

    stored = manager.created[0]
    assert stored["event_time"] == "2026-08-20T09:30:00+08:00"
    assert stored["event_time_end"] == "2026-08-20T09:45:00+08:00"
    assert "created" not in stored
    assert "last_active" not in stored


@pytest.mark.asyncio
async def test_bucket_manager_persists_event_time_separately_from_created(test_config):
    manager = BucketManager(test_config, embedding_engine=None)
    event_time = "2026-08-20T09:30:00+08:00"
    event_time_end = "2026-08-20T09:45:00+08:00"

    bucket_id = await manager.create(
        content="一条带来源时间的历史记忆",
        imported=True,
        source_tool="import",
        event_time=event_time,
        event_time_end=event_time_end,
    )
    bucket = await manager.get(bucket_id)
    metadata = bucket["metadata"]

    assert metadata["event_time"] == event_time
    assert metadata["event_time_end"] == event_time_end
    assert metadata["created"] != event_time
    assert metadata["last_active"] == metadata["created"]


@pytest.mark.asyncio
async def test_bucket_manager_does_not_invent_event_time_when_source_has_none(test_config):
    manager = BucketManager(test_config, embedding_engine=None)

    bucket_id = await manager.create(
        content="一条没有来源时间的历史记忆",
        imported=True,
        source_tool="import",
    )
    bucket = await manager.get(bucket_id)
    metadata = bucket["metadata"]

    assert "event_time" not in metadata
    assert "event_time_end" not in metadata
