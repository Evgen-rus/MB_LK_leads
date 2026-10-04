import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("agent_ssh", Path(__file__).parents[1] / "scripts" / "leadrecord_agent_ssh.py")
ssh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ssh)


@pytest.mark.parametrize("command", ["", "bash", "lkctl-compute projects list", "lkctl analytics download --group 1 --id 1 --output /tmp/x", "fetch 1 ../x", "fetch 1 0", "lkctl capabilities; id"])
def test_rejects_unapproved_commands(monkeypatch, capsys, command):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command)
    monkeypatch.setattr(ssh.subprocess, "run", lambda *a, **k: pytest.fail("must not execute"))
    assert ssh.main() == 1
    assert "SSH_COMMAND_REJECTED" in capsys.readouterr().out


def test_allowed_command_uses_argv_without_shell(monkeypatch):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "lkctl analytics plan --group 1")
    seen = []
    class Result:
        returncode = 0
    monkeypatch.setattr(ssh.subprocess, "run", lambda args, **kwargs: seen.append((args, kwargs)) or Result())
    assert ssh.main() == 0
    assert seen == [(["/usr/local/bin/lkctl", "analytics", "plan", "--group", "1"], {"cwd": ssh.ROOT})]
