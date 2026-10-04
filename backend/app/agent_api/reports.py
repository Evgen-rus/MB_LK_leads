"""Download only a registered analysis report; never accept a filesystem path."""
from .contracts import AgentError
from ..lead_analytics import db, router as pipeline
from ..lead_analytics.export_history import analysis_download_filename, analysis_report_path, list_exports


def report_download(params):
    group_id, export_id = params.get("group_id"), params.get("export_id")
    if not group_id or not export_id:
        raise AgentError("INVALID_PARAMETERS", "Укажите group_id и export_id", 422)
    group = db.get_group(group_id)
    if not group:
        raise AgentError("GROUP_NOT_FOUND", "Группа аналитики не найдена", 404)
    item = next((row for row in list_exports(db.group_key(group_id)) if int(row["id"]) == export_id), None)
    if not item:
        raise AgentError("RESULT_NOT_FOUND", "Результат аналитики не найден", 404)
    path = analysis_report_path(item.get("report_file_name"))
    if not path or not path.is_file() or path.is_symlink():
        raise AgentError("REPORT_UNAVAILABLE", "Файл отчёта недоступен", 404)
    run = db.get_run(str(item.get("run_id"))) if item.get("run_id") else None
    name = run["group_name"] if run else group["name"]
    source_sheet_name = (item.get("settings") or {}).get("source_sheet_name")
    if not source_sheet_name and item.get("run_id"):
        source_sheet_name = pipeline._matched_source_sheet_name(str(item["run_id"]))
    return path, analysis_download_filename(item, str(name), source_sheet_name)
