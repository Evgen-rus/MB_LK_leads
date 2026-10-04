import json
import io

import pytest

from backend.app import agent_cli


TOKEN = "test-agent-token"


def call_cli(monkeypatch, capsys, argv, response=None, error=None):
    calls = []

    def fake_transport(url, method, token, payload):
        calls.append((url, method, token, payload))
        if error:
            raise error
        return response or {"ok": True, "action": "capabilities", "data": {}, "error": None,
                            "request_id": "req-1", "state": "complete"}

    monkeypatch.setattr(agent_cli, "_transport", fake_transport)
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("LK_AGENT_URL", raising=False)
    code = agent_cli.main(argv)
    return code, json.loads(capsys.readouterr().out), calls


def test_read_command_maps_to_get_query(monkeypatch, capsys):
    code, output, calls = call_cli(monkeypatch, capsys, [
        "leads", "stats", "--client", "17", "--from", "2026-09-01", "--to", "2026-09-30"
    ], {"ok": True, "action": "leads.stats", "data": {}, "error": None,
        "request_id": "req-1", "state": "complete"})

    assert code == 0
    assert output["action"] == "leads.stats"
    url, method, token, payload = calls[0]
    assert url == "http://127.0.0.1:8000/agent/v1/leads.stats?client_id=17&from_date=2026-09-01&to_date=2026-09-30"
    assert (method, token, payload) == ("GET", TOKEN, None)


def test_clients_find_encodes_query(monkeypatch, capsys):
    code, _, calls = call_cli(monkeypatch, capsys, ["clients", "find", "Рио-Люкс"])
    assert code == 0
    assert calls[0][0] == "http://127.0.0.1:8000/agent/v1/clients.find?q=%D0%A0%D0%B8%D0%BE-%D0%9B%D1%8E%D0%BA%D1%81"


def test_analytics_run_posts_period(monkeypatch, capsys):
    code, _, calls = call_cli(monkeypatch, capsys, [
        "analytics", "run", "--group", "8", "--period", "2026-09-01:2026-09-30"
    ])
    assert code == 0
    assert calls[0][1:] == ("POST", TOKEN, {
        "group_id": 8, "period_start": "2026-09-01", "period_end": "2026-09-30"
    })
    assert calls[0][0] == "http://127.0.0.1:8000/agent/v1/analytics.run"


def test_analytics_result_maps_run_id(monkeypatch, capsys):
    code, _, calls = call_cli(monkeypatch, capsys, ["analytics", "result", "--group", "8", "--run", "run-abc"])
    assert code == 0
    assert calls[0][0].endswith("/analytics.result?group_id=8&run_id=run-abc")


def test_needs_input_has_exit_code_two_and_scrubs_token(monkeypatch, capsys):
    response = {"ok": False, "action": "analytics.run", "data": {"token": TOKEN},
                "error": {"code": "UNKNOWN_STATUSES", "message": TOKEN},
                "request_id": "req-1", "state": "needs_input"}
    code, output, _ = call_cli(monkeypatch, capsys, ["analytics", "run", "--group", "8", "--run", "run-abc"], response)
    assert code == 2
    assert TOKEN not in json.dumps(output)
    assert output["state"] == "needs_input"


def test_http_needs_input_envelope_is_preserved_and_scrubbed(monkeypatch, capsys):
    body = json.dumps({"ok": False, "action": "analytics.run", "error": {"code": "UNKNOWN_STATUSES",
                     "message": TOKEN}, "request_id": "req-1", "state": "needs_input",
                     "data": {"statuses": ["new"]}}).encode()

    class FakeOpener:
        def open(self, request, timeout):
            raise agent_cli.urllib.error.HTTPError(request.full_url, 409, "Conflict", {}, io.BytesIO(body))

    monkeypatch.setattr(agent_cli.urllib.request, "build_opener", lambda *handlers: FakeOpener())
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("LK_AGENT_URL", raising=False)
    code = agent_cli.main(["analytics", "run", "--group", "8", "--run", "run-abc"])
    output = capsys.readouterr().out

    assert code == 2
    assert TOKEN not in output
    assert json.loads(output)["data"] == {"statuses": ["new"]}


def test_malformed_http_error_body_is_generic(monkeypatch, capsys):
    class FakeOpener:
        def open(self, request, timeout):
            raise agent_cli.urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {}, io.BytesIO(b"secret HTML"))

    monkeypatch.setattr(agent_cli.urllib.request, "build_opener", lambda *handlers: FakeOpener())
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("LK_AGENT_URL", raising=False)
    code = agent_cli.main(["capabilities"])
    output = capsys.readouterr().out

    assert code == 1
    assert "secret HTML" not in output
    assert json.loads(output)["error"]["code"] == "AGENT_UNAVAILABLE"


def test_auth_http_error_keeps_machine_code_but_exits_one(monkeypatch, capsys):
    body = json.dumps({"ok": False, "action": "capabilities", "error": {"code": "UNAUTHORIZED",
                     "message": "Требуется Agent token"}, "request_id": "req-1"}).encode()

    class FakeOpener:
        def open(self, request, timeout):
            raise agent_cli.urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(body))

    monkeypatch.setattr(agent_cli.urllib.request, "build_opener", lambda *handlers: FakeOpener())
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("LK_AGENT_URL", raising=False)
    code = agent_cli.main(["capabilities"])
    output = json.loads(capsys.readouterr().out)

    assert code == 1
    assert output["error"]["code"] == "UNAUTHORIZED"


def test_parser_error_is_json_and_does_not_echo_bad_argument(capsys):
    with pytest.raises(SystemExit) as exc:
        agent_cli.main(["clients", "list", "--token", "do-not-print"])
    output = capsys.readouterr().out
    assert exc.value.code == 2
    assert json.loads(output)["error"]["code"] == "INVALID_ARGUMENTS"
    assert "do-not-print" not in output


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8000", "http://example.com", "http://user:pass@localhost:8000",
    "http://localhost:8000/path", "http://localhost:8000/?token=x", "http://localhost:8000/#frag",
])
def test_rejects_unsafe_base_url(monkeypatch, capsys, url):
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("LK_AGENT_URL", url)
    monkeypatch.setattr(agent_cli, "_transport", lambda *args: pytest.fail("transport called"))
    code = agent_cli.main(["capabilities"])
    output = capsys.readouterr().out
    assert code == 1
    assert json.loads(output)["error"]["code"] == "CONFIGURATION_ERROR"
    assert "pass" not in output and "token=x" not in output


def test_transport_disables_redirects_and_proxies(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"ok":true}'

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(agent_cli.urllib.request, "build_opener", build_opener)
    result = agent_cli._transport("http://127.0.0.1:8000/agent/v1/capabilities", "GET", TOKEN, None)
    assert result["ok"] is True
    assert any(isinstance(handler, agent_cli._NoRedirect) for handler in captured["handlers"])
    assert agent_cli._NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://localhost/") is None
    proxy = next(handler for handler in captured["handlers"] if isinstance(handler, agent_cli.urllib.request.ProxyHandler))
    assert proxy.proxies == {}
    assert captured["request"].get_header("Authorization") == f"Bearer {TOKEN}"
