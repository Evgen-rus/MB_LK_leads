"""Small, JSON-only command line client for the local Agent API."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile


DEFAULT_URL = "http://127.0.0.1:8000"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _HTTPStatusError(Exception):
    def __init__(self, status: int, response: dict | None = None):
        self.status, self.response = status, response


class _DownloadError(Exception):
    def __init__(self, code: str, message: str, input_error: bool = False):
        self.code, self.message, self.input_error = code, message, input_error


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _error(code: str, message: str, action: str | None = None, state: str = "error") -> dict:
    return {
        "ok": False,
        "action": action,
        "data": None,
        "error": {"code": code, "message": message},
        "request_id": None,
        "state": state,
    }


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ожидалось целое число") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("значение должно быть не меньше 1")
    return number


def _limit_int(value: str) -> int:
    number = _positive_int(value)
    if number > 200:
        raise argparse.ArgumentTypeError("значение должно быть не больше 200")
    return number


def _nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ожидалось целое число") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("значение должно быть не меньше 0")
    return number


def _date(value: str) -> str:
    if not DATE_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("ожидалась дата YYYY-MM-DD")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ожидалась корректная дата YYYY-MM-DD") from exc
    return value


def _period(value: str) -> tuple[str, str]:
    parts = value.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("ожидался период YYYY-MM-DD:YYYY-MM-DD")
    return _date(parts[0]), _date(parts[1])


def _project_ids(value: str) -> list[int]:
    if value.strip().casefold() == "none":
        return []
    try:
        ids = [_positive_int(item.strip()) for item in value.split(",")]
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError("ожидался список ID через запятую") from exc
    if not ids or len(ids) != len(set(ids)):
        raise argparse.ArgumentTypeError("список ID пуст или содержит повторы")
    return ids


def _status_assignment(value: str) -> tuple[str, str]:
    status, separator, category = value.partition("=")
    status, category = status.strip(), category.strip()
    if not separator or value.count("=") != 1 or not status or not category:
        raise argparse.ArgumentTypeError("ожидалось STATUS=CATEGORY")
    return status, category


class _JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # Do not echo raw arguments: they could contain a credential by mistake.
        _json(_error("INVALID_ARGUMENTS", "Недопустимые аргументы; используйте --help"))
        raise SystemExit(2)


def _date_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from", dest="from_date", type=_date)
    parser.add_argument("--to", dest="to_date", type=_date)


def _page_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=_limit_int)
    parser.add_argument("--offset", type=_nonnegative_int)


def _id_option(parser: argparse.ArgumentParser, flag: str, dest: str) -> None:
    parser.add_argument(flag, dest=dest, required=True, type=_positive_int)


def _build_parser() -> _JSONArgumentParser:
    parser = _JSONArgumentParser(prog="lkctl", description="LeadRecord Agent API client")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("capabilities", help="список доступных операций").set_defaults(action="capabilities")

    overview = commands.add_parser("overview", help="сводка LeadRecord")
    overview.set_defaults(action="overview")
    overview.add_argument("--client", dest="client_id", type=_positive_int)
    _date_options(overview)

    clients = commands.add_parser("clients", help="операции с клиентами")
    client_commands = clients.add_subparsers(dest="client_command")
    clients_list = client_commands.add_parser("list", help="список клиентов")
    clients_list.set_defaults(action="clients.list")
    _date_options(clients_list)
    _page_options(clients_list)
    clients_find = client_commands.add_parser("find", help="поиск клиентов по имени")
    clients_find.set_defaults(action="clients.find")
    clients_find.add_argument("query")
    _date_options(clients_find)
    _page_options(clients_find)

    client = commands.add_parser("client", help="показатели клиента")
    client_commands = client.add_subparsers(dest="client_command")
    client_show = client_commands.add_parser("show", help="показать клиента")
    client_show.set_defaults(action="client.show")
    client_id = client_show.add_mutually_exclusive_group(required=True)
    client_id.add_argument("--id", dest="client_id", type=_positive_int)
    client_id.add_argument("--client", dest="client_id", type=_positive_int)
    _date_options(client_show)

    projects = commands.add_parser("projects", help="операции с проектами")
    project_commands = projects.add_subparsers(dest="projects_command")
    projects_list = project_commands.add_parser("list", help="список проектов клиента")
    projects_list.set_defaults(action="projects.list")
    _id_option(projects_list, "--client", "client_id")
    projects_list.add_argument("--q")
    _date_options(projects_list)
    _page_options(projects_list)

    project = commands.add_parser("project", help="показатели проекта")
    project_commands = project.add_subparsers(dest="project_command")
    project_show = project_commands.add_parser("show", help="показать проект")
    project_show.set_defaults(action="project.show")
    project_id = project_show.add_mutually_exclusive_group(required=True)
    project_id.add_argument("--id", dest="project_id", type=_positive_int)
    project_id.add_argument("--project", dest="project_id", type=_positive_int)
    project_stats = project_commands.add_parser("stats", help="статистика идентификаций проекта")
    project_stats.set_defaults(action="project.stats")
    stats_id = project_stats.add_mutually_exclusive_group(required=True)
    stats_id.add_argument("--id", dest="project_id", type=_positive_int)
    stats_id.add_argument("--project", dest="project_id", type=_positive_int)
    _date_options(project_stats)

    leads = commands.add_parser("leads", help="агрегаты идентификаций")
    lead_commands = leads.add_subparsers(dest="leads_command")
    leads_stats = lead_commands.add_parser("stats", help="статистика идентификаций")
    leads_stats.set_defaults(action="leads.stats")
    lead_scope = leads_stats.add_mutually_exclusive_group(required=True)
    lead_scope.add_argument("--client", dest="client_id", type=_positive_int)
    lead_scope.add_argument("--project", dest="project_id", type=_positive_int)
    _date_options(leads_stats)

    analytics = commands.add_parser("analytics", help="операции с аналитикой")
    analytics_commands = analytics.add_subparsers(dest="analytics_command")
    groups = analytics_commands.add_parser("groups", help="группы аналитики клиента")
    groups.set_defaults(action="analytics.groups")
    _id_option(groups, "--client", "client_id")
    _page_options(groups)
    history = analytics_commands.add_parser("history", help="история аналитики группы")
    history.set_defaults(action="analytics.history")
    _id_option(history, "--group", "group_id")
    _page_options(history)
    result = analytics_commands.add_parser("result", help="результат анализа или состояние запуска")
    result.set_defaults(action="analytics.result")
    _id_option(result, "--group", "group_id")
    result_id = result.add_mutually_exclusive_group(required=True)
    result_id.add_argument("--id", dest="export_id", type=_positive_int)
    result_id.add_argument("--run", dest="run_id")
    run = analytics_commands.add_parser("run", help="поставить анализ в штатную очередь")
    run.set_defaults(action="analytics.run")
    _id_option(run, "--group", "group_id")
    run_target = run.add_mutually_exclusive_group(required=True)
    run_target.add_argument("--period", type=_period, metavar="YYYY-MM-DD:YYYY-MM-DD")
    run_target.add_argument("--run", dest="run_id")

    plan = analytics_commands.add_parser("plan", help="план подготовки аналитики")
    plan.set_defaults(action="analytics.plan")
    _id_option(plan, "--group", "group_id")
    plan.add_argument("--period", type=_period, metavar="YYYY-MM-DD:YYYY-MM-DD")
    prepare = analytics_commands.add_parser("prepare", help="подготовить снимок аналитики")
    prepare.set_defaults(action="analytics.prepare")
    _id_option(prepare, "--group", "group_id")
    prepare.add_argument("--period", required=True, type=_period, metavar="YYYY-MM-DD:YYYY-MM-DD")
    prepare.add_argument(
        "--confirm-projects", type=_project_ids, metavar="ID,ID|none",
        help="явно подтвердить добавляемые ID; none — явно отказаться от всех новых проектов",
    )
    confirm = analytics_commands.add_parser("confirm-statuses", help="задать явные правила статусов")
    confirm.set_defaults(action="analytics.confirm-statuses")
    _id_option(confirm, "--group", "group_id")
    confirm.add_argument("--run", dest="run_id", required=True)
    confirm.add_argument("--assign", action="append", required=True, type=_status_assignment,
                         metavar="STATUS=CATEGORY")
    download = analytics_commands.add_parser("download", help="скачать XLSX отчёт")
    download.set_defaults(action="analytics.download")
    _id_option(download, "--group", "group_id")
    _id_option(download, "--id", "export_id")
    download.add_argument("--output", required=True)

    return parser


def _arguments_to_request(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[str, dict, dict | None]:
    action = args.action
    values = vars(args)
    if values.get("from_date") and values.get("to_date") and values["from_date"] > values["to_date"]:
        parser.error("invalid date order")

    if action == "analytics.run":
        body = {"group_id": args.group_id}
        if args.period:
            start, end = args.period
            if start > end:
                parser.error("invalid period")
            body.update(period_start=start, period_end=end)
        else:
            body["run_id"] = args.run_id
        return action, {}, body

    if action == "analytics.plan":
        params = {"group_id": args.group_id}
        if args.period:
            start, end = args.period
            if start > end:
                parser.error("invalid period")
            params.update(period_start=start, period_end=end)
        return action, params, None

    if action == "analytics.prepare":
        start, end = args.period
        if start > end:
            parser.error("invalid period")
        body = {"group_id": args.group_id, "period_start": start, "period_end": end}
        if args.confirm_projects is not None:
            body["confirmed_project_ids"] = args.confirm_projects
        return action, {}, body

    if action == "analytics.confirm-statuses":
        status_rules = {}
        for status, category in args.assign:
            if status in status_rules:
                parser.error("duplicate status")
            status_rules[status] = category
        return action, {}, {"group_id": args.group_id, "run_id": args.run_id,
                            "status_rules": status_rules}

    if action == "analytics.download":
        return action, {"group_id": args.group_id, "export_id": args.export_id}, None

    names = ("client_id", "project_id", "group_id", "export_id", "run_id", "from_date", "to_date", "limit", "offset", "q")
    params = {name: values[name] for name in names if values.get(name) is not None}
    if action == "clients.find":
        params["q"] = args.query
    return action, params, None


def _validate_base_url(value: str) -> str:
    if not value or value != value.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except (ValueError, TypeError):
        raise ValueError from None
    if not host or parsed.scheme.lower() != "http" or parsed.username is not None or parsed.password is not None:
        raise ValueError
    if parsed.query or parsed.fragment or parsed.path not in ("", "/") or parsed.netloc.endswith(":"):
        raise ValueError
    if port is not None and not 1 <= port <= 65535:
        raise ValueError
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback:
        raise ValueError
    return f"http://{parsed.netloc.rstrip('/')}"


def _http_error(exc: urllib.error.HTTPError) -> _HTTPStatusError:
    envelope = None
    try:
        body = json.loads(exc.read(1_000_000).decode("utf-8"))
        if isinstance(body, dict) and body.get("ok") is False:
            envelope = body
    except Exception:
        pass
    return _HTTPStatusError(exc.code, envelope)


def _transport(url: str, method: str, token: str, payload: dict | None, timeout: int = 15) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from None
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise ValueError
    return result


def _download_path(value: str) -> pathlib.Path:
    try:
        path = pathlib.Path(value)
        if not path.is_absolute() or not path.name or path.name in (".", ".."):
            raise _DownloadError("INVALID_OUTPUT_PATH", "Укажите абсолютный путь к файлу XLSX", True)
        if not path.parent.is_dir():
            raise _DownloadError("INVALID_OUTPUT_PATH", "Каталог для файла не существует", True)
        if os.path.lexists(path):
            raise _DownloadError("OUTPUT_EXISTS", "Файл назначения уже существует", True)
    except (OSError, TypeError, ValueError):
        raise _DownloadError("INVALID_OUTPUT_PATH", "Недопустимый путь к файлу XLSX", True) from None
    return path


def _download_file(url: str, token: str, value: str) -> dict:
    output = _download_path(value)
    fd, temporary = tempfile.mkstemp(prefix=".lkctl-", suffix=".tmp", dir=output.parent)
    size, checksum, request_id, expected_size = 0, hashlib.sha256(), None, None
    try:
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, application/json",
        }, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from None
        with response:
            if response.getcode() != 200:
                raise _DownloadError("DOWNLOAD_FAILED", "Agent API не вернул полный XLSX отчёт")
            request_id = response.headers.get("X-Request-Id")
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    expected_size = int(length)
                except ValueError:
                    raise _DownloadError("DOWNLOAD_FAILED", "Некорректный размер XLSX файла") from None
                if expected_size < 0:
                    raise _DownloadError("DOWNLOAD_FAILED", "Некорректный размер XLSX файла")
                if expected_size > MAX_DOWNLOAD_BYTES:
                    raise _DownloadError("DOWNLOAD_TOO_LARGE", "Файл превышает лимит загрузки")
            with os.fdopen(fd, "wb") as target:
                fd = -1
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise _DownloadError("DOWNLOAD_TOO_LARGE", "Файл превышает лимит загрузки")
                    target.write(chunk)
                    checksum.update(chunk)
        if expected_size is not None and size != expected_size:
            raise _DownloadError("DOWNLOAD_FAILED", "Размер XLSX файла не совпадает с ответом сервера")
        try:
            with zipfile.ZipFile(temporary) as workbook:
                names = set(workbook.namelist())
        except (OSError, zipfile.BadZipFile):
            raise _DownloadError("INVALID_XLSX", "Сервер вернул некорректный XLSX файл") from None
        if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
            raise _DownloadError("INVALID_XLSX", "Сервер вернул некорректный XLSX файл")
        try:
            os.link(temporary, output)
        except FileExistsError:
            raise _DownloadError("OUTPUT_EXISTS", "Файл назначения уже существует", True) from None
        return {"ok": True, "action": "analytics.download",
                "data": {"path": str(output), "bytes": size, "sha256": checksum.hexdigest()},
                "error": None, "request_id": request_id, "state": "complete"}
    except _DownloadError:
        raise
    except OSError:
        raise _DownloadError("DOWNLOAD_FAILED", "Не удалось сохранить XLSX файл") from None
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _scrub(value: object, token: str) -> object:
    if isinstance(value, str):
        return value.replace(token, "[REDACTED]") if token else value
    if isinstance(value, list):
        return [_scrub(item, token) for item in value]
    if isinstance(value, dict):
        return {
            (key.replace(token, "[REDACTED]") if token else key): _scrub(item, token)
            for key, item in value.items()
        }
    return value


def _timed_out(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout)) or (
        isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout))
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "action", None):
        parser.error("missing command")
    action, params, payload = _arguments_to_request(args, parser)

    token = os.environ.get("LK_AGENT_TOKEN", "")
    if not token or token != token.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in token):
        _json(_error("CONFIGURATION_ERROR", "LK_AGENT_TOKEN не настроен", action))
        return 1
    try:
        base_url = _validate_base_url(os.environ.get("LK_AGENT_URL", DEFAULT_URL))
    except ValueError:
        _json(_error("CONFIGURATION_ERROR", "LK_AGENT_URL должен указывать на локальный HTTP адрес", action))
        return 1

    query = urllib.parse.urlencode(params)
    url = f"{base_url}/agent/v1/{action}"
    if query:
        url = f"{url}?{query}"
    try:
        if action == "analytics.download":
            result = _download_file(url, token, args.output)
        else:
            timeout = 120 if action == "analytics.prepare" else 15
            result = _transport(url, "POST" if payload is not None else "GET", token, payload, timeout)
    except _DownloadError as exc:
        _json(_error(exc.code, exc.message, action, "input" if exc.input_error else "error"))
        return 2 if exc.input_error else 1
    except _HTTPStatusError as exc:
        is_input = exc.status == 422 or (exc.response or {}).get("state") in ("input", "needs_input")
        if exc.response is not None:
            _json(_scrub(exc.response, token))
        else:
            message = "Agent API отклонил запрос" if is_input else "Agent API недоступен"
            _json(_error("AGENT_REQUEST_REJECTED" if is_input else "AGENT_UNAVAILABLE", message, action,
                         "input" if is_input else "error"))
        return 2 if is_input else 1
    except Exception as exc:
        if action == "analytics.prepare" and _timed_out(exc):
            _json(_error("PREPARE_TIMEOUT", "Ответ не получен вовремя; запуск мог быть создан. Проверьте его состояние перед повтором", action))
            return 1
        _json(_error("AGENT_UNAVAILABLE", "Не удалось связаться с Agent API", action))
        return 1

    _json(_scrub(result, token))
    if result["ok"]:
        return 0
    return 2 if result.get("state") in ("input", "needs_input") else 1


if __name__ == "__main__":
    raise SystemExit(main())
