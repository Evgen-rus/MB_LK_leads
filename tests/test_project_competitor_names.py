from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import crud, models, schemas  # noqa: E402
from backend.app.competitor_names import find_competitor_name, normalize_competitor_names  # noqa: E402
from backend.app.providers import prostats  # noqa: E402
from backend.app.time_utils import now_msk  # noqa: E402

_MISSING = object()


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
    with local_session() as db:
        db.add(models.User(id=2, login="client", password_hash="x", role="client", created_at=now_msk()))
        db.commit()
        yield db
    engine.dispose()


def _create_item(*, source: str = "B4", competitor_names=None):
    return schemas.CreateProjectItem(
        name="Project",
        tag="Project",
        collectionSource="Сайты",
        dataSourceCode=source,
        dataLimit=10,
        status="Активен",
        sites=["example.test"],
        days=["Вт"],
        competitorNames=competitor_names,
    )


def _update(*, competitor_names=_MISSING):
    values = {
        "name": "Project renamed",
        "tag": "Project renamed",
        "status": "Активен",
        "dataLimit": 10,
        "sites": ["example.test"],
        "days": ["Вт"],
    }
    if competitor_names is not _MISSING:
        values["competitorNames"] = competitor_names
    return schemas.ProjectUpdate(**values)


def test_competitor_phone_normalization_and_lookup():
    mapping = normalize_competitor_names(
        {"8 (343) 123-45-67": "  Конкурент  ", "73431234568": "   "}
    )

    assert mapping == {"73431234567": "Конкурент"}
    assert find_competitor_name(mapping, "+7 (343) 123-45-67;extra") == "Конкурент"
    assert find_competitor_name(mapping, "not-a-phone") is None
    with pytest.raises(ValueError, match="номер телефона"):
        normalize_competitor_names({"123": "Конкурент"})
    with pytest.raises(ValueError, match="разные названия"):
        normalize_competitor_names({"83431234567": "Один", "73431234567": "Другой"})
    with pytest.raises(ValueError, match="не должно превышать"):
        normalize_competitor_names({"73431234567": "x" * 121})
    with pytest.raises(ValueError, match="не более 500"):
        normalize_competitor_names({f"7{number:010d}": "Конкурент" for number in range(501)})


def test_mapping_persists_for_b4_and_omitted_updates_preserve_it(session):
    competitor_names = {"8 (343) 123-45-67": "Конкурент"}
    project_out = crud.create_projects(
        session,
        [_create_item(competitor_names=competitor_names)],
        user_id=2,
        provider_ids=["provider-1"],
    )[0]
    project = session.get(models.Project, project_out.id)
    assert project.competitor_names == {"73431234567": "Конкурент"}
    assert project_out.competitorNames == project.competitor_names

    update_out = crud.update_project(session, project.id, _update(), user_id=2)
    assert update_out.competitorNames == project.competitor_names

    update_out = crud.update_project(
        session,
        project.id,
        _update(competitor_names={"73431234569": "Другой"}),
        user_id=2,
    )
    assert update_out.competitorNames == {"73431234569": "Другой"}

    admin_update = schemas.AdminProjectUpdate(
        name="Project admin rename",
        tag="Project admin rename",
        status="Активен",
        deliveryStatus="Активна",
        dataLimit=10,
        sites=["example.test"],
        days=["Вт"],
        competitorNames={},
    )
    admin_out = crud.admin_update_project(session, project.id, admin_update, admin_user_id=1)
    assert admin_out.competitorNames is None


def test_other_channels_do_not_store_competitor_mapping(session):
    project_out = crud.create_projects(
        session,
        [_create_item(source="B1", competitor_names={"73431234567": "Конкурент"})],
        user_id=2,
        provider_ids=["provider-2"],
    )[0]

    assert session.get(models.Project, project_out.id).competitor_names is None


def test_startup_evolution_adds_mapping_to_existing_sqlite_database():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY)"))

    main_path = Path(__file__).resolve().parents[1] / "backend" / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_ensure_project_competitor_names_column"
    )
    extracted = ast.Module(body=[function], type_ignores=[])
    namespace = {"engine": engine, "inspect": inspect, "text": text}
    exec(compile(ast.fix_missing_locations(extracted), str(main_path), "exec"), namespace)

    namespace[function.name]()
    namespace[function.name]()
    assert "competitor_names" in {column["name"] for column in inspect(engine).get_columns("projects")}
    engine.dispose()


def test_mapping_survives_queued_project_update_snapshot(session):
    project_out = crud.create_projects(session, [_create_item()], user_id=2, provider_ids=["provider-3"])[0]
    update = _update(competitor_names={"73431234567": "Конкурент"})
    job = crud.create_project_operation(
        session,
        client_id=2,
        operation_type="bulk_update",
        actor_user_id=2,
        actor_role="client",
        items=[{
            "projectId": project_out.id,
            "providerProjectId": "provider-3",
            "projectName": project_out.name,
            "stateSnapshot": {},
            "payloadSnapshot": {"action": "update", "update": update.model_dump()},
        }],
    )

    queued_item = session.execute(
        select(models.ProjectOperationItem).where(models.ProjectOperationItem.operation_id == job.id)
    ).scalar_one()
    restored = schemas.ProjectUpdate(**queued_item.payload_snapshot["update"])
    assert restored.competitorNames == {"73431234567": "Конкурент"}


def test_competitor_mapping_is_not_sent_to_prostats(monkeypatch):
    monkeypatch.setattr(prostats, "_get_token", lambda: "test-token")
    create_item = _create_item(competitor_names={"73431234567": "Конкурент"})
    update = _update(competitor_names={"73431234567": "Конкурент"})
    project = SimpleNamespace(collection_source="Сайты", data_source_code="B4")

    create_payload = prostats.build_create_payload(create_item)
    update_payload = prostats.build_update_payload("provider-4", project, update)
    assert "competitorNames" not in create_payload
    assert "competitorNames" not in update_payload
