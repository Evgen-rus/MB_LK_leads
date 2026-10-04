"""Isolated HTTP boundary. Existing admin/JWT dependencies remain unchanged."""
import hmac
import logging
import os
import time
import uuid
from datetime import date

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException

from .contracts import AgentError, CAPABILITIES, discovery

logger = logging.getLogger("app.agent")


class ReadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: int | None = Field(None, ge=1)
    project_id: int | None = Field(None, ge=1)
    group_id: int | None = Field(None, ge=1)
    export_id: int | None = Field(None, ge=1)
    run_id: str | None = Field(None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    from_date: date | None = None
    to_date: date | None = None
    q: str | None = Field(None, max_length=200)
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class RunParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: int | None = Field(None, ge=1)
    run_id: str | None = Field(None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    period_start: date | None = None
    period_end: date | None = None


def _failure(request, error):
    body = {"ok": False, "action": getattr(request.state, "action", None),
            "error": {"code": error.code, "message": error.message},
            "request_id": request.state.agent_request_id}
    if error.state:
        body["state"] = error.state
    if error.data is not None:
        body["data"] = error.data
    return JSONResponse(jsonable_encoder(body), status_code=error.status)


def build_app(get_db, settings):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    operations = {name: (scope, params) for name, scope, _, params in CAPABILITIES}

    @app.middleware("http")
    async def boundary(request, call_next):
        started = time.monotonic()
        request.state.agent_request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        action = request.url.path.rsplit("/", 1)[-1]
        request.state.action = action if action in operations else None
        scope = operations.get(action, ("unknown", []))[0]
        audit_params = {}
        try:
            tokens = [(os.getenv("LK_AGENT_READ_TOKEN", ""), {"read"}),
                      (os.getenv("LK_AGENT_COMPUTE_TOKEN", ""), {"read", "compute"})]
            configured = [token for token, _ in tokens if token]
            if not configured or len(configured) != len(set(configured)):
                raise AgentError("AGENT_DISABLED", "Agent Interface не настроен", 503)
            header = request.headers.get("authorization", "")
            supplied = header[7:] if header.startswith("Bearer ") else ""
            scopes = next((scopes for token, scopes in tokens
                           if token and hmac.compare_digest(supplied.encode(), token.encode())), None)
            if scopes is None:
                raise AgentError("UNAUTHORIZED", "Требуется Agent token", 401)
            request.state.agent_scopes = scopes
            if scope != "unknown" and scope not in scopes:
                raise AgentError("SCOPE_REQUIRED", "Требуется scope compute", 403)
            # Only parsed numeric IDs, dates and pagination are audit-safe. No query text.
            for key, value in request.query_params.items():
                if key in {"client_id", "project_id", "group_id", "export_id", "limit", "offset"}:
                    if value.isascii() and value.isdigit() and len(value) < 12:
                        audit_params[key] = int(value)
                elif key in {"from_date", "to_date"}:
                    try:
                        audit_params[key] = date.fromisoformat(value).isoformat()
                    except ValueError:
                        pass
            request.state.audit_params = audit_params
            response = await call_next(request)
        except AgentError as error:
            response = _failure(request, error)
        except Exception:
            # Exceptions can contain credentials or uploaded data; never log their text.
            response = _failure(request, AgentError("INTERNAL_ERROR", "Внутренняя ошибка Agent Interface", 500))
        logger.info("action=%s scope=%s params=%s outcome=%s duration_ms=%d rid=%s",
                    request.state.action or "unknown", scope, audit_params,
                    "success" if response.status_code < 400 else "failure",
                    int((time.monotonic() - started) * 1000), request.state.agent_request_id)
        response.headers["X-Request-Id"] = request.state.agent_request_id
        return response

    @app.exception_handler(AgentError)
    async def agent_error(request, error):
        return _failure(request, error)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return _failure(request, AgentError("INVALID_PARAMETERS", "Некорректные параметры", 422))

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        code = {404: "CAPABILITY_NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(error.status_code, "REQUEST_FAILED")
        return _failure(request, AgentError(code, "Операция недоступна", error.status_code))

    def success(request, data):
        return {"ok": True, "action": request.state.action, "data": data,
                "request_id": request.state.agent_request_id}

    @app.get("/{action}")
    def read(action: str, request: Request, session=Depends(get_db)):
        if action not in operations or operations[action][0] != "read":
            raise AgentError("CAPABILITY_NOT_FOUND", "Read capability не найдена", 404)
        raw = dict(request.query_params)
        if set(raw) - set(operations[action][1]):
            raise AgentError("INVALID_PARAMETERS", "Неизвестные параметры операции", 422)
        try:
            params = ReadParams.model_validate(raw).model_dump(mode="json", exclude_none=True)
        except ValidationError:
            raise AgentError("INVALID_PARAMETERS", "Некорректные параметры", 422) from None
        if action == "capabilities":
            return success(request, discovery(request.state.agent_scopes))
        if action.startswith("analytics."):
            from .analytics import execute_read
            data = execute_read(action, session, params)
        else:
            from .service import execute_read
            data = execute_read(action, session, params, settings)
        return success(request, data)

    @app.post("/analytics.run")
    def run(payload: RunParams, request: Request):
        from .analytics import execute_run
        params = payload.model_dump(mode="json", exclude_none=True)
        request.state.audit_params.update({key: value for key, value in params.items()
                                           if key in {"group_id", "period_start", "period_end"}})
        return success(request, execute_run(params))

    return app
