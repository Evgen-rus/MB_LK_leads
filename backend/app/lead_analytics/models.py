from dataclasses import dataclass


@dataclass
class ColumnMapping:
    sheet_name: str
    date_column: str | None = None
    phone_column: str | None = None
    channel_column: str | None = None
    source_column: str | None = None
    status_column: str | None = None
    comment_column: str | None = None
    lkid_column: str | None = None
    project_column: str | None = None


@dataclass
class StatusRule:
    pattern: str
    match_type: str
    group_name: str
    subgroup_name: str | None = None
    comment: str | None = None
    priority: int = 100
    project_code: str | None = None
