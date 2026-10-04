"""Small public contract shared by the operational API services."""


class AgentError(Exception):
    def __init__(self, code, message, status=400, state=None, data=None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.state, self.data = state, data


# Parameters are names in the HTTP interface, not implementation details.
CAPABILITIES = [
    ("capabilities", "read", "Доступные операции и параметры", []),
    ("overview", "read", "Сводка и клиенты, требующие внимания", ["from_date", "to_date", "client_id"]),
    ("clients.list", "read", "Сводка клиентов", ["from_date", "to_date", "limit", "offset"]),
    ("clients.find", "read", "Поиск клиентов по имени", ["q", "from_date", "to_date", "limit", "offset"]),
    ("client.show", "read", "Показатели клиента", ["client_id", "from_date", "to_date"]),
    ("projects.list", "read", "Проекты, включая архивные и удалённые", ["client_id", "q", "from_date", "to_date", "limit", "offset"]),
    ("project.show", "read", "Проект без сырых телефонов", ["project_id"]),
    ("project.stats", "read", "Идентификации проекта по дням", ["project_id", "from_date", "to_date"]),
    ("leads.stats", "read", "Агрегаты идентификаций", ["client_id", "project_id", "from_date", "to_date"]),
    ("analytics.groups", "read", "Группы аналитики клиента", ["client_id", "limit", "offset"]),
    ("analytics.history", "read", "История готовых анализов группы", ["group_id", "limit", "offset"]),
    ("analytics.result", "read", "Агрегаты готового отчёта или состояние запуска", ["group_id", "run_id", "export_id"]),
    ("analytics.run", "compute", "Поставить подготовленный запуск в штатную очередь; иначе needs_input", ["group_id", "run_id", "period_start", "period_end"]),
]

PARAMETERS = {
    "client_id": {"type": "integer", "minimum": 1},
    "project_id": {"type": "integer", "minimum": 1},
    "group_id": {"type": "integer", "minimum": 1},
    "export_id": {"type": "integer", "minimum": 1},
    "run_id": {"type": "string", "description": "ID уже подготовленного запуска"},
    "q": {"type": "string", "max_length": 200},
    "from_date": {"type": "date", "description": "YYYY-MM-DD; по умолчанию сегодня в SHEETS_TZ"},
    "to_date": {"type": "date", "description": "Включительно; максимум 366 дней"},
    "period_start": {"type": "date"},
    "period_end": {"type": "date"},
    "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
    "offset": {"type": "integer", "default": 0, "minimum": 0},
}

REQUIRED = {
    "clients.find": ["q"], "client.show": ["client_id"],
    "projects.list": ["client_id"], "project.show": ["project_id"],
    "project.stats": ["project_id"], "analytics.groups": ["client_id"],
    "analytics.history": ["group_id"], "analytics.result": ["group_id"],
}

INPUT_CHOICES = {
    "leads.stats": [["client_id"], ["project_id"]],
    "analytics.result": [["export_id"], ["run_id"]],
    "analytics.run": [["run_id"], ["group_id", "period_start", "period_end"]],
}


def discovery(scopes):
    return {"version": "1", "scopes": sorted(scopes), "capabilities": [
        {"name": name, "scope": scope, "description": description,
         "available": scope in scopes, "method": "POST" if scope == "compute" else "GET",
         "path": f"/agent/v1/{name}", "parameters": {p: PARAMETERS[p] for p in params},
         "required": REQUIRED.get(name, []), "input_choices": INPUT_CHOICES.get(name, [])}
        for name, scope, description, params in CAPABILITIES
    ]}
