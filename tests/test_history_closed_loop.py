"""End-to-end contracts for usable, source-backed history recall.

These tests intentionally cross the import, chronology, retrieval, and evidence
boundaries.  Unit tests for each component are not enough if the model cannot
walk from one imported memory back to the exact transcript chunk that produced
it.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from import_memory import ImportEngine
from ombrebrain.storage.source_store import SourceStore
from tools.breath import dispatch as breath


class _Dehydrator:
    api_available = True


class _Decay:
    async def ensure_started(self):
        return None

    def calculate_score(self, _metadata):
        return 1.0


@pytest.mark.asyncio
async def test_conversation_import_persists_event_time_title_and_source_evidence(
    test_config,
    bucket_mgr,
    monkeypatch,
):
    store = SourceStore(bucket_mgr.base_dir)
    engine = ImportEngine(
        test_config,
        bucket_mgr,
        _Dehydrator(),
        source_store=store,
    )
    transcript = (
        "[测试用户] 周三先讨论迁移。\n"
        "[AI] 周四开始验收。\n"
        "[测试用户] 这个决定发生在正式上线之前。"
    )

    async def extract(_content):
        return [{
            "name": "",
            "content": "迁移先于上线。",
            "domain": ["回忆"],
            "tags": ["时间线"],
            "importance": 8,
        }]

    monkeypatch.setattr(engine, "_extract_memories", extract)
    assert await engine._process_single_chunk(
        {
            "content": transcript,
            "timestamp_start": "2026-08-20T21:00:00+08:00",
            "timestamp_end": "2026-08-20T21:05:00+08:00",
            "turn_count": 3,
        },
        preserve_raw=False,
    )

    buckets = await bucket_mgr.list_all(include_archive=False)
    assert len(buckets) == 1
    bucket = buckets[0]
    metadata = bucket["metadata"]

    assert metadata["title"] == "迁移先于上线。"
    assert metadata["event_time"] == "2026-08-20T21:00:00+08:00"
    assert metadata["event_time_end"] == "2026-08-20T21:05:00+08:00"
    assert metadata["created"] != metadata["event_time"]
    assert metadata["source_refs"][0]["ranges"] == [[1, 3]]
    assert store.read(metadata["source_refs"][0]["ref"]) == transcript


@pytest.mark.asyncio
async def test_exact_bucket_date_filter_uses_event_time_not_import_time(
    test_config,
    bucket_mgr,
    fake_embedding_engine,
    monkeypatch,
):
    bucket_id = await bucket_mgr.create(
        content="迁移决定发生在正式上线之前。",
        title="迁移时间线",
        source_tool="import",
        imported=True,
        event_time="2026-08-20T21:00:00+08:00",
        event_time_end="2026-08-20T21:05:00+08:00",
    )
    monkeypatch.setattr(rt, "config", {"surfacing": {}}, raising=False)
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "embedding_engine", fake_embedding_engine, raising=False)
    monkeypatch.setattr(rt, "embedding_outbox", None, raising=False)
    monkeypatch.setattr(rt, "decay_engine", _Decay(), raising=False)
    monkeypatch.setattr(rt, "logger", MagicMock(), raising=False)
    monkeypatch.setattr(rt, "fire_webhook", None, raising=False)
    monkeypatch.setattr(rt, "mark_op", None, raising=False)

    result = await breath(
        query=bucket_id,
        date_from="2026-08-20",
        date_to="2026-08-20",
    )

    assert f"[bucket_id:{bucket_id}]" in result
    assert "迁移决定发生在正式上线之前。" in result


@pytest.mark.asyncio
async def test_date_filtered_catalog_is_a_source_read_locator(
    bucket_mgr,
    monkeypatch,
):
    historical_id = await bucket_mgr.create(
        content="历史正文不会出现在目录。",
        name="历史迁移",
        title="迁移时间线",
        domain=["回忆"],
        imported=True,
        source_tool="import",
        event_time="2026-08-20T21:00:00+08:00",
        event_time_end="2026-08-20T21:05:00+08:00",
    )
    await bucket_mgr.create(
        content="另一日正文也不会出现。",
        name="另一日",
        title="另一日",
        domain=["回忆"],
        imported=True,
        source_tool="import",
        event_time="2026-08-21T09:00:00+08:00",
    )
    monkeypatch.setattr(rt, "config", {"surfacing": {}}, raising=False)
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "decay_engine", _Decay(), raising=False)
    monkeypatch.setattr(rt, "logger", MagicMock(), raising=False)
    monkeypatch.setattr(rt, "fire_webhook", None, raising=False)
    monkeypatch.setattr(rt, "mark_op", None, raising=False)

    result = await breath(
        catalog=True,
        date_from="2026-08-20",
        date_to="2026-08-20",
    )

    assert "迁移时间线" in result
    assert "另一日" not in result
    assert f"[bucket_id:{historical_id}]" in result
    assert "[title:迁移时间线]" in result
    assert "[event_time:2026-08-20T21:00:00+08:00]" in result
    assert "历史正文不会出现在目录" not in result


@pytest.mark.asyncio
async def test_source_read_pages_verbatim_evidence_and_is_state_neutral(
    bucket_mgr,
    monkeypatch,
):
    from tools.source_read import dispatch as source_read

    store = SourceStore(bucket_mgr.base_dir)
    original = "[测试用户] 第一页开始。\n" + ("完整原文不能静默截断。" * 1200)
    ref = store.put(original)
    bucket_id = await bucket_mgr.create(
        content="分页回读测试记忆。",
        title="分页回读",
        imported=True,
        source_tool="import",
        event_time="2026-08-20T21:00:00+08:00",
        source_refs=[{
            "ref": ref,
            "ranges": [[1, len(original.splitlines()) or 1]],
        }],
    )
    before = (await bucket_mgr.get(bucket_id))["metadata"]
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "source_store", store, raising=False)
    monkeypatch.setattr(rt, "logger", MagicMock(), raising=False)

    pages: list[str] = []
    cursor = 0
    while True:
        page = await source_read(
            bucket_id,
            "分页回读",
            scope="event",
            cursor=cursor,
            max_tokens=220,
        )
        header, body = page.split("\n\n", 1)
        assert "untrusted_source=true" in header
        assert f"source_refs={ref}" in header
        assert f"source_sha256={ref.removeprefix('src_')}" in header
        pages.append(body)
        cursor = int(header.split("next_cursor=", 1)[1].splitlines()[0])
        if cursor == 0:
            break

    assert "".join(pages) == original
    after = (await bucket_mgr.get(bucket_id))["metadata"]
    for field in ("created", "last_active", "activation_count", "importance"):
        assert after[field] == before[field]


@pytest.mark.asyncio
async def test_source_read_requires_exact_title_and_never_expands_empty_event_range(
    bucket_mgr,
    monkeypatch,
):
    from tools.source_read import dispatch as source_read

    store = SourceStore(bucket_mgr.base_dir)
    ref = store.put("整份敏感原文")
    bucket_id = await bucket_mgr.create(
        content="整理后的记忆",
        title="精确标题",
        source_refs=[{"ref": ref, "ranges": []}],
    )
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "source_store", store, raising=False)

    wrong_title = await source_read(bucket_id, "差一个字")
    assert "标题不匹配" in wrong_title
    event = await source_read(bucket_id, "精确标题", scope="event")
    assert "未声明事件原文范围" in event
    assert "整份敏感原文" not in event


@pytest.mark.asyncio
async def test_source_read_rejects_tampered_evidence(bucket_mgr, monkeypatch):
    from tools.source_read import dispatch as source_read

    store = SourceStore(bucket_mgr.base_dir)
    ref = store.put("不可篡改的证据")
    bucket_id = await bucket_mgr.create(
        content="证据完整性测试",
        title="完整性",
        source_refs=[{"ref": ref, "ranges": [[1, 1]]}],
    )
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "source_store", store, raising=False)
    (store.root / f"{ref}.source").write_text("已经被改掉", encoding="utf-8")

    result = await source_read(bucket_id, "完整性")

    assert "完整性校验失败" in result
    assert "已经被改掉" not in result


@pytest.mark.asyncio
async def test_chatgpt_import_to_dated_catalog_to_source_read_closed_loop(
    test_config,
    bucket_mgr,
    monkeypatch,
):
    from tools.source_read import dispatch as source_read

    config = {**test_config, "human": "测试用户"}
    store = SourceStore(bucket_mgr.base_dir)
    engine = ImportEngine(
        config,
        bucket_mgr,
        _Dehydrator(),
        source_store=store,
    )
    export = json.dumps({
        "messages": [
            {
                "role": "user",
                "content": {"parts": ["周三先讨论迁移。"]},
                "timestamp": "2026-08-20T21:00:00+08:00",
            },
            {
                "role": "assistant",
                "content": {"parts": ["周四开始验收。"]},
                "timestamp": "2026-08-20T21:01:00+08:00",
            },
        ]
    }, ensure_ascii=False)

    async def extract(_content):
        return [{
            "name": "迁移时间线",
            "content": "迁移决定发生在正式上线之前。",
            "domain": ["回忆"],
            "tags": ["时间线"],
            "importance": 8,
        }]

    monkeypatch.setattr(engine, "_extract_memories", extract)
    result = await engine.start(export, filename="chatgpt-history.json")
    assert result["status"] == "completed"

    monkeypatch.setattr(rt, "config", {"surfacing": {}}, raising=False)
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "decay_engine", _Decay(), raising=False)
    monkeypatch.setattr(rt, "logger", MagicMock(), raising=False)
    monkeypatch.setattr(rt, "fire_webhook", None, raising=False)
    monkeypatch.setattr(rt, "mark_op", None, raising=False)
    monkeypatch.setattr(rt, "source_store", store, raising=False)
    catalog = await breath(
        catalog=True,
        date_from="2026-08-20",
        date_to="2026-08-20",
    )
    bucket = (await bucket_mgr.list_all(include_archive=False))[0]
    bucket_id = bucket["id"]
    assert f"[bucket_id:{bucket_id}]" in catalog
    assert "[title:迁移时间线]" in catalog
    assert "[source_available:true]" in catalog

    page = await source_read(bucket_id, "迁移时间线")
    header, body = page.split("\n\n", 1)
    assert "next_cursor=0" in header
    assert body == "[测试用户] 周三先讨论迁移。\n[AI] 周四开始验收。"
    assert bucket["metadata"]["source_refs"][0]["ref"] in header

    # Re-importing the same export remains idempotent at both layers.
    second = await engine.start(export, filename="chatgpt-history.json")
    assert second["memories_created"] == 0
    assert second["memories_skipped"] == 1
    assert len(await bucket_mgr.list_all(include_archive=False)) == 1
    assert len(list(store.root.glob("*.source"))) == 1
