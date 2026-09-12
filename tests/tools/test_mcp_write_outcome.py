"""Registry dispatch must not blindly replay a mutation after a lost response."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tools import mcp_tool, mcp_tool_handlers as handlers
from tools.registry import ToolRegistry


@pytest.mark.parametrize("hint", [False, None, "true"])
@pytest.mark.parametrize("failure", [RuntimeError("connection closed"), TimeoutError(),
                                     RuntimeError("Session terminated"),
                                     handlers._StdioChildExited("exited mid-call")])
def test_unknown_mutation_outcome_never_replayed(monkeypatch, hint, failure):
    session = SimpleNamespace(call_tool=AsyncMock(side_effect=failure))
    server = SimpleNamespace(session=session, _rpc_lock=asyncio.Lock())
    monkeypatch.setitem(mcp_tool._tool_read_only_hints, "writes", {"change": hint})
    monkeypatch.setattr(handlers, "_trust_gate_check", lambda *a: None)
    monkeypatch.setattr(handlers, "_check_circuit_breaker", lambda *a: None)
    monkeypatch.setattr(handlers, "_acquire_call_server", lambda *a: (server, None))
    monkeypatch.setattr(handlers._loop, "_run_on_mcp_loop", lambda call, **kw: asyncio.run(call()))
    monkeypatch.setattr(handlers._loop, "_signal_reconnect", Mock())
    monkeypatch.setattr(mcp_tool, "_bump_server_error", Mock())
    retry = Mock(side_effect=AssertionError("mutation replay attempted"))
    monkeypatch.setattr(handlers, "_handle_session_expired_and_retry", retry)
    name = "mcp__writes__change"
    registry = ToolRegistry()
    registry.register(name=name, toolset="mcp-writes",
                      schema={"name": name, "description": "Test write", "parameters": {"type": "object"}},
                      handler=handlers._make_tool_handler("writes", "change", 3))
    result = json.loads(registry.dispatch(name, {}))
    assert result["outcome"] == "unknown"
    assert result["replayed"] is False
    assert "authoritative state" in result["error"]
    session.call_tool.assert_awaited_once()
    retry.assert_not_called()
