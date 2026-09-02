"""行动闭环工具单测：create_ticket / submit_ticket + 开关条件注册。

注意：不用 importlib.reload 验证开关——reload 会重执行 `from app.config import settings`
把 monkeypatch 的模块属性覆盖回真实 settings；改为 patch 模块属性后直接调
build_default_registry() 验证两种状态。
"""
import asyncio
from types import SimpleNamespace


def _settings(monkeypatch, enable: bool):
    import app.services.agent_tools as svc
    monkeypatch.setattr(svc, "settings", SimpleNamespace(TICKET_ACTION_LOOP_ENABLE=enable))


def test_create_ticket_tool(monkeypatch):
    import app.services.agent_tools as svc
    from app.services import ticket_lifecycle_service as tl
    _settings(monkeypatch, True)

    async def fake_create(db, **kw):
        assert kw["source_ref"] == "qa:conv-1"
        return {"id": "t123", "title": kw["task"][:200], "status": "draft"}
    monkeypatch.setattr(tl, "create_ticket", fake_create)
    out = asyncio.run(svc._t_create_ticket(
        None, None, task="1号主变由运行转检修", device="1号主变",
        steps=["停电", "验电"], safety=["戴绝缘手套"], risks=["触电"],
        sourceRef="qa:conv-1", tenant="default", creator="alice"))
    assert "t123" in out and "草稿" in out


def test_create_ticket_requires_task(monkeypatch):
    import app.services.agent_tools as svc
    _settings(monkeypatch, True)
    out = asyncio.run(svc._t_create_ticket(None, None, task="", tenant=None))
    assert "task 不能为空" in out


def test_submit_ticket_tool(monkeypatch):
    import app.services.agent_tools as svc
    from app.services import ticket_lifecycle_service as tl
    _settings(monkeypatch, True)

    async def fake_submit(db, ticket_id, *, tenant="default"):
        return {"id": ticket_id, "status": "reviewed", "reviewScore": 92}
    monkeypatch.setattr(tl, "submit_for_review", fake_submit)
    out = asyncio.run(svc._t_submit_ticket(None, None, ticketId="t1", tenant="default"))
    assert "92" in out and "reviewed" in out


def test_submit_ticket_not_found(monkeypatch):
    import app.services.agent_tools as svc
    from app.services import ticket_lifecycle_service as tl
    _settings(monkeypatch, True)

    async def fake_submit(db, ticket_id, *, tenant="default"):
        raise ValueError("票据不存在")
    monkeypatch.setattr(tl, "submit_for_review", fake_submit)
    out = asyncio.run(svc._t_submit_ticket(None, None, ticketId="missing", tenant="default"))
    assert "提交失败" in out and "票据不存在" in out


def test_registry_flag_off(monkeypatch):
    """开关关：新工具不注册，老工具不受影响。"""
    import app.services.agent_tools as svc
    _settings(monkeypatch, False)
    reg = svc.build_default_registry()
    assert reg.get("create_ticket") is None
    assert reg.get("submit_ticket") is None
    assert reg.get("draft_ticket") is not None


def test_registry_flag_on(monkeypatch):
    import app.services.agent_tools as svc
    _settings(monkeypatch, True)
    reg = svc.build_default_registry()
    assert reg.get("create_ticket") is not None
    assert reg.get("submit_ticket") is not None


def test_creator_reserved_injected_from_ctx(monkeypatch):
    """creator 是运行时保留参数：LLM args 里的 creator 被剥掉，注入登录态用户名。"""
    from app.services.agent_runtime import Tool, ToolRegistry

    seen = {}

    async def fake_audit(*args, **kwargs):
        return None

    async def handler(db, model_type, task="", tenant=None, creator=""):
        seen.update(creator=creator, tenant=tenant)
        return "ok"

    monkeypatch.setattr(
        "app.services.agent_tool_audit_service.log_tool_call", fake_audit,
    )
    registry = ToolRegistry()
    registry.register(Tool("fake_create", "d", {}, handler))
    result, error = asyncio.run(registry.run(
        None, None, "fake_create",
        {"task": "x", "creator": "spoofed"},
        ctx={"username": "alice", "tenant": "t1"},
    ))
    assert error is False and result == "ok"
    assert seen == {"creator": "alice", "tenant": "t1"}


def test_creator_stripped_without_ctx(monkeypatch):
    """无登录态（老链路 ctx=None）：creator 一律剥掉，落 handler 默认空串。"""
    from app.services.agent_runtime import Tool, ToolRegistry

    seen = {}

    async def handler(db, model_type, task="", creator=""):
        seen.update(creator=creator)
        return "ok"

    registry = ToolRegistry()
    registry.register(Tool("fake_create", "d", {}, handler))
    result, error = asyncio.run(registry.run(
        None, None, "fake_create", {"task": "x", "creator": "spoofed"},
    ))
    assert error is False and result == "ok"
    assert seen == {"creator": ""}
