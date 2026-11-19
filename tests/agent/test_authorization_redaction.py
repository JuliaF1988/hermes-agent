"""Keep synthetic authentication material out of model-facing and persisted data."""

import json
import logging
import secrets
from types import SimpleNamespace

import pytest

from agent.redact import RedactingFormatter, redact_sensitive_text, redact_terminal_output
from agent.redact_auth import redact_authorization_payload, redact_authorization_text
from agent.trajectory import save_trajectory


@pytest.fixture
def credential():
    return secrets.token_urlsafe(36)


def assert_absent(output, credential):
    assert all(part not in output for part in (credential, credential[:6], credential[-4:]))


@pytest.mark.parametrize("header", ["Authorization", "Proxy-Authorization", "AuThOrIzAtIoN"])
@pytest.mark.parametrize("token_form", ["opaque", "legacy", "ellipsis", "vendor"])
def test_header_mask_is_complete(header, token_form, credential):
    token = {
        "opaque": credential,
        "legacy": f"{credential[:6]}...{credential[-4:]}",
        "ellipsis": f"{credential[:6]}…{credential[-4:]}",
        "vendor": "ghp_" + credential,
    }[token_form]
    text = f"{header}: Bearer {token}\nStatus: 403"
    expected = f"{header}: Bearer ***\nStatus: 403"
    assert redact_sensitive_text(text, force=True) == expected
    assert redact_terminal_output(text, force=True) == expected
    assert_absent(expected, credential)


@pytest.mark.parametrize("encodings", [0, 1, 2, 3])
def test_encoded_json_preserves_following_facts_and_escapes(credential, encodings):
    value = {"headers": {"pRoXy-aUtHoRiZaTiOn": f'Basic {credential}\\"opaque\\'}, "status": 403}
    text = json.dumps(value)
    for _ in range(encodings):
        text = json.dumps(text)
    result = redact_sensitive_text(text, force=True)
    assert_absent(result, credential)
    assert redact_sensitive_text(result, force=True) == result
    for _ in range(encodings + 1):
        result = json.loads(result)
    assert result == {"headers": {"pRoXy-aUtHoRiZaTiOn": "Basic ***"}, "status": 403}


def test_bearer_without_header_and_unambiguous_short_header(credential):
    for value in (credential, f"{credential[:6]}...{credential[-4:]}"):
        result = redact_authorization_text(f"Bearer {value}")
        assert result == "Bearer ***"
        assert_absent(result, credential)
    assert redact_authorization_text('{"Authorization":"Bearer short"}') == '{"Authorization":"Bearer ***"}'
    assert redact_authorization_text('Authorization: "Bearer short"') == 'Authorization: "Bearer ***"'


@pytest.mark.parametrize("text", [
    "The bearer of bad news recorded a failed authorization check.",
    '{"authorization_status":"denied","provider":"sentinel","count":247}',
    '{"Authorization":"Bearer ${env:PROFILE_TOKEN}"}',
    "Authorization:\nStatus: 403",
    "NonAuthorization: status prose",
])
def test_preserve_prose_statuses_and_inert_source_references(text):
    assert redact_authorization_text(text) == text


def test_mcp_tool_result_before_clipping_and_serialization(credential):
    from tools.mcp_tool_content import _truncate_mcp_text_result
    from tools.mcp_tool_handlers import _render_call_tool_result

    value = "prefix " + "x" * 35 + "\nAuthorization: Bearer " + credential * 3 + "\nstatus: ready"
    clipped = _truncate_mcp_text_result(value, max_chars=90)
    assert_absent(clipped, credential)
    assert clipped.endswith("status: ready")
    assert "TRUNCATED" not in clipped

    data = {"message": '{"Authorization":"Bearer ' + credential + '"}', "status": "ready"}
    result = SimpleNamespace(content=[], structured_content=data, meta={"Authorization": credential})
    payload = _render_call_tool_result(result, "local_fixture")
    assert_absent(payload, credential)
    assert json.loads(payload) == {
        "_meta": {"Authorization": "***"},
        "result": {"message": '{"Authorization":"Bearer ***"}', "status": "ready"},
    }
    assert credential in data["message"]  # only the outbound copy is scrubbed


def test_logger_and_trajectory_never_persist_header_fragments(credential, tmp_path):
    message = '{"headers":{"Authorization":"Bearer ' + credential + '"},"result":"reachable"}'
    record = logging.LogRecord("synthetic", logging.DEBUG, "", 0, "HTTP %s", (message,), None)
    formatted = RedactingFormatter("%(message)s").format(record)
    assert_absent(formatted, credential)
    assert "reachable" in formatted

    transcript = tmp_path / "trajectory.jsonl"
    save_trajectory([{"from": "tool", "value": message}], "fixture", True, filename=transcript)
    persisted = transcript.read_text()
    assert_absent(persisted, credential)
    assert json.loads(persisted)["conversations"][0]["from"] == "tool"
    assert "reachable" in persisted


def test_wire_payload_remains_intact_for_transport(credential):
    source = {"headers": {"Authorization": "Bearer " + credential}, "status": 200}
    assert redact_authorization_payload(source) == {
        "headers": {"Authorization": "Bearer ***"}, "status": 200
    }
    assert source["headers"]["Authorization"] == "Bearer " + credential


def test_tool_handoff_and_reopened_bot_history(credential, tmp_path, monkeypatch):
    import hermes_cli.plugins as plugins
    import model_tools
    from hermes_state import SessionDB

    message = json.dumps({"headers": {"Authorization": "Bearer " + credential}, "status": "ready"})
    seen = {}

    def capture_hook(name, **kwargs):
        if "result" in kwargs:
            seen[name] = kwargs["result"]
        return []

    monkeypatch.setattr(model_tools.registry, "dispatch", lambda *args, **kwargs: message)
    monkeypatch.setattr(plugins, "has_hook", lambda name: name in {"post_tool_call", "transform_tool_result"})
    monkeypatch.setattr(plugins, "invoke_hook", capture_hook)
    result = model_tools.handle_function_call("web_search", {"q": "synthetic"}, task_id="synthetic")
    assert json.loads(result)["status"] == "ready"
    assert seen.keys() == {"post_tool_call", "transform_tool_result"}
    for content in [result, *seen.values()]:
        assert_absent(content, credential)
    path = tmp_path / "bot-history.db"
    store = SessionDB(path)
    try:
        store.create_session("synthetic-bot-session", "telegram")
        store.append_message("synthetic-bot-session", "tool", result, tool_name="web_search", tool_call_id="synthetic-call")
    finally:
        store.close()
    reopened = SessionDB(path)
    try:
        messages = reopened.get_messages("synthetic-bot-session")
    finally:
        reopened.close()
    assert len(messages) == 1
    assert_absent(messages[0]["content"], credential)
    for file in tmp_path.glob("bot-history.db*"):
        assert_absent(file.read_bytes().decode(errors="ignore"), credential)
