"""Regression tests for preserving source chronology during history imports."""

import pytest

from import_memory import ImportEngine


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
