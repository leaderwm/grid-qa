"""/qa/feedback sources_json 落库上限单测（回归）。

背景：sources 列表（List[dict]）原本无条数/长度上限，超 MySQL TEXT 64KB 时
commit 抛 DataError → 反馈接口 500 且整条反馈丢失。服务端现做条数封顶 + 逐条收缩。
"""
import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.feedback import Feedback
from app.services import feedback_service


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Feedback.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _huge_sources(n=60, pad=20000):
    """60 条 × 每条 20KB → 原始序列化远超 64KB TEXT 上限。"""
    return [{"doc_name": f"规程-{i}.pdf", "snippet": "x" * pad} for i in range(n)]


@pytest.mark.asyncio
async def test_record_feedback_caps_sources_json(session_factory):
    async with session_factory() as db:
        await feedback_service.record_feedback(
            db, conversation_id="c1", query="q", answer="a", feedback="dislike",
            username="u1", sources=_huge_sources(),
        )
        row = (await db.execute(select(Feedback))).scalars().one()

    assert len(row.sources_json.encode("utf-8")) <= 60000
    assert len(json.loads(row.sources_json)) <= 50  # 条数封顶


@pytest.mark.asyncio
async def test_record_feedback_normal_sources_intact(session_factory):
    """正常小 sources 不受截断影响，按归一化形状原样落库。"""
    sources = [{"doc_name": "规程A.pdf", "snippet": "相关条款"}]
    async with session_factory() as db:
        await feedback_service.record_feedback(
            db, conversation_id="c2", query="q", answer="a", feedback="like",
            username="u1", sources=sources,
        )
        row = (await db.execute(select(Feedback))).scalars().one()

    expected = feedback_service.normalize_sources(sources, "")
    assert json.loads(row.sources_json) == expected
