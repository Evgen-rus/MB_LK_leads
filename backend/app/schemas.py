"""
Файл: backend/app/schemas.py
Назначение: Pydantic-схемы ввода/вывода для API.
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


Day = Literal['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
ProjectStatus = Literal['Активен', 'На паузе', 'Удалён']
DeliveryStatus = Literal['Активна', 'На модерации', 'Отключена']
CollectionSource = Literal['Сайты','Звонки','СМС','Ретросайты','Ретрозвонки','Пересечение']
DataSourceCode = Literal['B1','B2','B3','B4','UNMAPPED']


class CreateProjectItem(BaseModel):
    name: str
    tag: str
    collectionSource: CollectionSource
    dataSourceCode: DataSourceCode
    dataLimit: int
    status: ProjectStatus
    regionMode: Optional[Literal['include','exclude']] = None
    regions: List[str] = []
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    days: List[Day]


class CreateProjectsPayload(BaseModel):
    items: List[CreateProjectItem]


class ProjectUpdate(BaseModel):
    name: str
    tag: str
    status: ProjectStatus
    dataLimit: int
    regionMode: Optional[Literal['include','exclude']] = None
    regions: List[str] = []
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    days: List[Day]


class ProjectOut(BaseModel):
    id: int
    status: ProjectStatus
    deliveryStatus: DeliveryStatus
    name: str
    tag: str
    collectionSource: CollectionSource
    dataSourceCode: DataSourceCode
    regionMode: Optional[Literal['include','exclude']] = None
    regions: Optional[List[str]] = None
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    dataLimit: int
    numbersToday: int
    numbersTotal: int
    numbersPeriod: int = 0
    daysReceived: str
    sourcesCount: int
    createdAt: str

    class Config:
        from_attributes = True


class CreateProjectsOut(BaseModel):
    items: List[ProjectOut]
    warning: Optional[str] = None


class ProjectListOut(BaseModel):
    items: List[ProjectOut]
    total: int


# -------- Админские схемы (все клиенты) --------
class UserInfo(BaseModel):
    """Информация о владельце для админских ответов."""
    id: int
    login: str
    name: Optional[str] = None


class SelfProfileOut(BaseModel):
    """Краткий профиль текущего пользователя."""
    id: int
    login: str
    name: Optional[str] = None


class ClientProfileOut(BaseModel):
    name: str
    inn: str
    phone: str
    contact: Optional[str] = None

    class Config:
        from_attributes = True


class AdminClientCreateIn(BaseModel):
    name: str
    inn: str
    phone: str
    contact: Optional[str] = None
    login: Optional[str] = None
    password: Optional[str] = None


class AdminClientCreateOut(BaseModel):
    user: UserInfo
    profile: ClientProfileOut
    login: str
    password: str


class AdminClientUpdateIn(BaseModel):
    name: Optional[str] = None
    inn: Optional[str] = None
    phone: Optional[str] = None
    contact: Optional[str] = None
    login: Optional[str] = None
    password: Optional[str] = None


class AdminClientUpdateOut(BaseModel):
    user: UserInfo
    profile: ClientProfileOut
    login: str
    password: Optional[str] = None


class AdminProjectOut(ProjectOut):
    """Проект с информацией о владельце (для админа)."""
    user: UserInfo


class AdminProjectListOut(BaseModel):
    items: List[AdminProjectOut]
    total: int


class AdminProjectUpdate(BaseModel):
    """Обновление проекта админом (включая delivery_status)."""
    name: str
    tag: str
    status: ProjectStatus
    deliveryStatus: DeliveryStatus
    dataLimit: int
    regionMode: Optional[Literal['include','exclude']] = None
    regions: List[str] = []
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    days: List[Day]


class ClientErrorIn(BaseModel):
    message: str = Field(..., description="Сообщение ошибки")
    stack: Optional[str] = Field(None, description="Стек ошибки")
    url: Optional[str] = Field(None, description="URL страницы")
    userAgent: Optional[str] = Field(None, description="User-Agent браузера")
    level: Optional[Literal['error','warn','info']] = 'error'
    time: Optional[str] = None



class LeadOut(BaseModel):
    ext_id: int
    project_id: int
    created_at: str
    imported_at: str
    phone: str
    utm_campaign: Optional[str] = None
    source: Optional[str] = None

    class Config:
        from_attributes = True


class LeadsListOut(BaseModel):
    items: List[LeadOut]
    total: int


# -------- Админские схемы для лидов --------
class AdminLeadOut(BaseModel):
    ext_id: int
    project_id: int
    created_at: str
    imported_at: str
    phone: str
    utm_campaign: Optional[str] = None
    source: Optional[str] = None
    project_name: Optional[str] = None
    user: UserInfo

    class Config:
        from_attributes = True


class AdminLeadsListOut(BaseModel):
    items: List[AdminLeadOut]
    total: int


# -------- Черный список --------
class BlacklistPhoneOut(BaseModel):
    id: int
    phone: str
    createdAt: str

    class Config:
        from_attributes = True


class BlacklistAddIn(BaseModel):
    phones: List[str]


class BlacklistListOut(BaseModel):
    items: List[BlacklistPhoneOut]
    total: int


# -------- Админские схемы для черного списка --------
class AdminBlacklistPhoneOut(BaseModel):
    id: int
    phone: str
    createdAt: str
    user: UserInfo

    class Config:
        from_attributes = True


class AdminBlacklistListOut(BaseModel):
    items: List[AdminBlacklistPhoneOut]
    total: int


# -------- История изменений проектов --------
class ProjectHistoryItem(BaseModel):
    id: int
    action: Literal['create', 'update', 'delete']
    createdAt: str
    description: str


class AdminProjectHistoryItem(BaseModel):
    id: int
    action: Literal['create', 'update', 'delete']
    createdAt: str
    description: str
    user: Optional[UserInfo] = None
    status: Literal['pending', 'done'] = 'pending'
    projectSnapshot: Optional[dict] = None


class AdminProjectHistoryListOut(BaseModel):
    items: List[AdminProjectHistoryItem]
    total: int


# -------- Админ: изменения клиентов --------
class AdminChangeOut(BaseModel):
    id: int
    projectId: Optional[int] = None
    projectName: Optional[str] = None
    batchId: Optional[str] = None
    createdAt: str
    action: Literal['create', 'update', 'delete', 'blacklist_add', 'blacklist_delete'] = 'update'
    description: str
    status: Literal['pending', 'done'] = 'pending'
    projectSnapshot: Optional[dict] = None
    beforeSnapshot: Optional[dict] = None
    changedFields: Optional[List[str]] = None


class AdminClientChangesOut(BaseModel):
    user: UserInfo
    items: List[AdminChangeOut]


class AdminClientChangesSummaryItem(BaseModel):
    user: UserInfo
    pendingChanges: int
    pendingCreates: int = 0
    pendingBlacklistAdds: int = 0
    pendingBlacklistDeletes: int = 0
    pendingTotal: int = 0


class AdminClientChangesSummaryListOut(BaseModel):
    items: List[AdminClientChangesSummaryItem]


# -------- Сводка по клиентам --------
class AdminClientSummaryItem(BaseModel):
    user: UserInfo
    profile: Optional[ClientProfileOut] = None
    projectCount: int
    totalLimit: int
    usedTotal: int
    usedPeriod: int
    remaining: int
    pendingChanges: int = 0
    pendingCreates: int = 0
    # Новые поля по номерам
    numbersCredited: int | None = None
    numbersDebited: int | None = None
    numbersBalance: int | None = None
    numbersUsed: int | None = None
    numbersUsedPeriod: int | None = None


class AdminClientSummaryTotals(BaseModel):
    clients: int
    projects: int
    totalLimit: int
    usedTotal: int
    usedPeriod: int
    remaining: int
    pendingCreates: int = 0
    pendingChanges: int = 0


class AdminClientsSummaryOut(BaseModel):
    items: List[AdminClientSummaryItem]
    totals: AdminClientSummaryTotals


# -------- Баланс по номерам (идентификациям) --------
BalanceOpType = Literal['credit', 'debit']


class BalanceOperationOut(BaseModel):
    id: int
    clientId: int
    amount: int
    type: BalanceOpType
    comment: Optional[str] = None
    createdAt: str
    createdBy: UserInfo


class BalanceOperationCreateIn(BaseModel):
    amount: int
    type: BalanceOpType
    comment: Optional[str] = None


class ClientBalanceSummaryOut(BaseModel):
    clientId: int
    credited: int
    debited: int
    manualBalance: int
    usedTotal: int
    usedPeriod: int
    remaining: int
    debt: bool
    periodFrom: str
    periodTo: str


class ClientBalanceOpsListOut(BaseModel):
    items: List[BalanceOperationOut]
    total: int


# -------- Отчёты (история экспортов) --------
class ReportOut(BaseModel):
    id: int
    createdAt: str
    fromDate: str
    toDate: str
    projectIds: Optional[str] = None
    format: str


class ReportListOut(BaseModel):
    items: List[ReportOut]
    total: int


# -------- Админские схемы для отчётов --------
class AdminReportOut(BaseModel):
    id: int
    createdAt: str
    fromDate: str
    toDate: str
    projectIds: Optional[str] = None
    format: str
    user: UserInfo                # кто сформировал
    client: Optional[UserInfo] = None  # для какого клиента


class AdminReportListOut(BaseModel):
    items: List[AdminReportOut]
    total: int


class AdminCreateReportIn(BaseModel):
    fromDate: str
    toDate: str
    projectIds: Optional[List[int]] = None
    format: str = "csv"
    clientId: int


class ClientCreateReportIn(BaseModel):
    fromDate: str
    toDate: str
    projectIds: Optional[List[int]] = None
    format: str = "csv"


# -------- Поддержка (сообщение в Telegram) --------
class SupportMessageIn(BaseModel):
    phone: str
    text: str
