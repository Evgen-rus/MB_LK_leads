import json
import io

import pytest

from backend.app import agent_cli


TOKEN = "test-agent-token"


def call_cli(monkeypatch, capsys, argv, response=None, error=None):
    calls = []

    def fake_transport(url, method, token, payload, timeout=15):
        calls.append((url, method, token, payload, timeout))
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
    url, method, token, payload, _ = calls[0]
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
    assert calls[0][1:4] == ("POST", TOKEN, {
        "group_id": 8, "period_start": "2026-09-01", "period_end": "2026-09-30"
    })
    assert calls[0][4] == 15
    assert calls[0][0] == "http://127.0.0.1:8000/agent/v1/analytics.run"


def test_analytics_result_maps_run_id(monkeypatch, capsys):
    code, _, calls = call_cli(monkeypatch, capsys, ["analytics", "result", "--group", "8", "--run", "run-abc"])
    assert code == 0
    assert calls[0][0].endswith("/analytics.result?group_id=8&run_id=run-abc")


def test_analytics_plan_maps_optional_period_to_get(monkeypatch, capsys):
    code, _, calls = call_cli(monkeypatch, capsys, [
        "analytics", "plan", "--group", "8", "--period", "2026-09-01:2026-09-30"
    ])
    assert code == 0
    assert calls[0][0] == ("http://127.0.0.1:8000/agent/v1/analytics.plan?group_id=8"
                           "&period_start=2026-09-01&period_end=2026-09-30")
    assert (calls[0][1], calls[0][3]) == ("GET", None)


def test_analytics_prepare_posts_explicit_project_ids_and_uses_long_timeout(monkeypatch, capsys):
    code, _, calls = call_cli(monkeypatch, capsys, [
        "analytics", "prepare", "--group", "8", "--period", "2026-09-01:2026-09-30",
        "--confirm-projects", "2, 4"
    ])
    assert code == 0
    assert calls[0][0] == "http://127.0.0.1:8000/agent/v1/analytics.prepare"
    assert calls[0][1:4] == ("POST", TOKEN, {
        "group_id": 8, "period_start": "2026-09-01", "period_end": "2026-09-30",
        "confirmed_project_ids": [2, 4],
    })
    assert calls[0][4] == 120


def test_analytics_confirm_statuses_posts_explicit_mapping(monkeypatch, capsys):
    code, _, calls = call_cli(monkeypatch, capsys, [
        "analytics", "confirm-statuses", "--group", "8", "--run", "run-abc",
        "--assign", "new=missed", "--assign", "completed=quality"
    ])
    assert code == 0
    assert calls[0][0] == "http://127.0.0.1:8000/agent/v1/analytics.confirm-statuses"
    assert calls[0][1:4] == ("POST", TOKEN, {
        "group_id": 8, "run_id": "run-abc", "status_rules": {"new": "missed", "completed": "quality"}
    })


def _xlsx_bytes():
    content = io.BytesIO()
    with agent_cli.zipfile.ZipFile(content, "w") as workbook:
        workbook.writestr("[Content_Types].xml", "<Types/>")
        workbook.writestr("xl/workbook.xml", "<workbook/>")
    return content.getvalue()


def test_analytics_download_streams_valid_xlsx_without_stdout_binary(monkeypatch, capsys, tmp_path):
    payload = _xlsx_bytes()
    captured = {}

    class FakeResponse:
        headers = {"Content-Length": str(len(payload)), "X-Request-Id": "download-1"}

        def __init__(self):
            self.stream = io.BytesIO(payload)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def getcode(self):
            return 200

        def read(self, size=-1):
            return self.stream.read(size)

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(agent_cli.urllib.request, "build_opener", build_opener)
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("LK_AGENT_URL", raising=False)
    output_path = tmp_path / "report.xlsx"
    code = agent_cli.main(["analytics", "download", "--group", "8", "--id", "42",
                           "--output", str(output_path)])
    stdout = capsys.readouterr().out
    result = json.loads(stdout)

    assert code == 0
    assert output_path.read_bytes() == payload
    assert stdout.encode()[:2] != b"PK"
    assert result["data"] == {"path": str(output_path), "bytes": len(payload),
                              "sha256": agent_cli.hashlib.sha256(payload).hexdigest()}
    assert result["request_id"] == "download-1"
    request = captured["request"]
    assert request.full_url.endswith("/analytics.download?group_id=8&export_id=42")
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert captured["timeout"] == 30
    assert any(isinstance(handler, agent_cli._NoRedirect) for handler in captured["handlers"])
    proxy = next(handler for handler in captured["handlers"] if isinstance(handler, agent_cli.urllib.request.ProxyHandler))
    assert proxy.proxies == {}


def test_analytics_download_does_not_overwrite_or_call_api(monkeypatch, capsys, tmp_path):
    output_path = tmp_path / "existing.xlsx"
    output_path.write_bytes(b"keep")
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.setattr(agent_cli.urllib.request, "build_opener", lambda *args: pytest.fail("request sent"))
    code = agent_cli.main(["analytics", "download", "--group", "8", "--id", "42",
                           "--output", str(output_path)])
    result = json.loads(capsys.readouterr().out)
    assert code == 2
    assert result["error"]["code"] == "OUTPUT_EXISTS"
    assert output_path.read_bytes() == b"keep"


def test_analytics_download_requires_absolute_output_path(monkeypatch, capsys):
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.setattr(agent_cli.urllib.request, "build_opener", lambda *args: pytest.fail("request sent"))
    code = agent_cli.main(["analytics", "download", "--group", "8", "--id", "42", "--output", "report.xlsx"])
    result = json.loads(capsys.readouterr().out)
    assert code == 2
    assert result["error"]["code"] == "INVALID_OUTPUT_PATH"


def test_analytics_download_rejects_oversize_response_without_partial_file(monkeypatch, capsys, tmp_path):
    class FakeResponse:
        headers = {"Content-Length": "11"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def getcode(self):
            return 200

        def read(self, size=-1):
            pytest.fail("oversize body should not be read")

    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse()

    monkeypatch.setattr(agent_cli, "MAX_DOWNLOAD_BYTES", 10)
    monkeypatch.setattr(agent_cli.urllib.request, "build_opener", lambda *args: FakeOpener())
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("LK_AGENT_URL", raising=False)
    output_path = tmp_path / "too-large.xlsx"
    code = agent_cli.main(["analytics", "download", "--group", "8", "--id", "42",
                           "--output", str(output_path)])
    result = json.loads(capsys.readouterr().out)

    assert code == 1
    assert result["error"]["code"] == "DOWNLOAD_TOO_LARGE"
    assert list(tmp_path.iterdir()) == []


def test_invalid_download_removes_temporary_file(monkeypatch, capsys, tmp_path):
    class FakeResponse:
        headers = {}

        def __init__(self):
            self.stream = io.BytesIO(b"not an xlsx")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def getcode(self):
            return 200

        def read(self, size=-1):
            return self.stream.read(size)

    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse()

    monkeypatch.setattr(agent_cli.urllib.request, "build_opener", lambda *args: FakeOpener())
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("LK_AGENT_URL", raising=False)
    output_path = tmp_path / "bad.xlsx"
    code = agent_cli.main(["analytics", "download", "--group", "8", "--id", "42",
                           "--output", str(output_path)])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result["error"]["code"] == "INVALID_XLSX"
    assert list(tmp_path.iterdir()) == []


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


def test_prepare_timeout_warns_without_retry_or_exception_text(monkeypatch, capsys):
    calls = []

    def timed_out(url, method, token, payload, timeout=15):
        calls.append((method, timeout))
        raise agent_cli.urllib.error.URLError(TimeoutError(TOKEN))

    monkeypatch.setattr(agent_cli, "_transport", timed_out)
    monkeypatch.setenv("LK_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("LK_AGENT_URL", raising=False)
    code = agent_cli.main(["analytics", "prepare", "--group", "8", "--period", "2026-09-01:2026-09-30"])
    stdout = capsys.readouterr().out
    result = json.loads(stdout)

    assert code == 1
    assert calls == [("POST", 120)]
    assert result["error"]["code"] == "PREPARE_TIMEOUT"
    assert TOKEN not in stdout


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
