#!/usr/bin/env python3
"""Restricted SSH entry point for Rick; use as authorized_keys forced command."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path("/opt/MB_LK_leads")
READ = {("capabilities",), ("clients", "find"), ("clients", "list"),
        ("client", "show"), ("projects", "list"), ("analytics", "groups"),
        ("analytics", "plan"), ("analytics", "history"), ("analytics", "result")}
COMPUTE = {("analytics", "prepare"), ("analytics", "run"), ("analytics", "confirm-statuses")}


def main():
    try:
        args = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
        if len(args) == 3 and args[0] == "fetch" and all(x.isascii() and x.isdigit() and int(x) > 0 for x in args[1:]):
            directory = ROOT / ".tmp" / "agent-exports"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(dir=directory) as tmp:
                path = Path(tmp) / "report.xlsx"
                reply = subprocess.run(["/usr/local/bin/lkctl-compute", "analytics", "download",
                                        "--group", args[1], "--id", args[2], "--output", str(path)],
                                       capture_output=True)
                if reply.returncode:
                    sys.stdout.buffer.write(reply.stdout)
                    return reply.returncode
                with path.open("rb") as stream:
                    while chunk := stream.read(65536):
                        sys.stdout.buffer.write(chunk)
                return 0
        if not args or args[0] not in {"lkctl", "lkctl-compute"}:
            raise ValueError()
        tail = tuple(args[1:3]) if len(args) > 2 else tuple(args[1:])
        allowed = READ if args[0] == "lkctl" else COMPUTE
        if tail not in allowed:
            raise ValueError()
        # Validate against the same parser; no shell or arbitrary local command.
        sys.path.insert(0, str(ROOT))
        from backend.app.agent_cli import _build_parser
        _build_parser().parse_args(args[1:])
        return subprocess.run(["/usr/local/bin/" + args[0], *args[1:]], cwd=ROOT).returncode
    except (ValueError, OSError):
        print(json.dumps({"ok": False, "error": {"code": "SSH_COMMAND_REJECTED", "message": "Команда недоступна"}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
