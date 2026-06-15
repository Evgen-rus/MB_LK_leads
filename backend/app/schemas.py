"""
Файл: backend/app/schemas.py
Назначение: Pydantic-схемы ввода/вывода для API.
"""
from datetime import datetime
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


Day = Literal['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
ProjectMutableStatus = Literal['Активен', 'На паузе', 'Удалён']
ProjectStatus = Literal['Активен', 'На паузе', 'Удалён', 'Блокировка оператора']
DeliveryStatus = Literal['Активна', 'На модерации', 'Отключена']
CollectionSource = Literal['Сайты','Звонки','СМС','Ретросайты','Ретрозвонки','Пересечение','Пиксель']
DataSourceCode = Literal['B1','B2','B3','B4','UNMAPPED']
LeadSource = Literal['provider', 'pixel']
UserRole = Literal['admin', 'client', 'agent']
ClientWorkStatus = Literal['В работе', 'Ждём оплату', 'Ждём данные', 'На согласовании', 'Пауза по клиенту', 'Неактивен']
ClientDataCollectionStatus = Literal['Нет проектов', 'Сбор активен', 'На паузе']
ClientFinanceStatus = Literal['Дожим 1', 'Дожим 2', 'Дожим 3', 'Долг']


def _normalize_project_phones(value: Optional[List[str]]) -> Optional[List[str]]:
    if value is None:
        return None
    normalized: List[str] = []
    seen = set()
    for raw_value in value:
        raw = str(raw_value or "").strip()
        if not raw:
            continue
        digits = re.sub(r"\D+", "", raw)
        if len(digits) != 11:
            raise ValueError(f'Некорректный номер телефона "{raw}": нужно ровно 11 цифр')
        if digits[0] == "8":
            digits = f"7{digits[1:]}"
        elif digits[0] != "7":
            raise ValueError(f'Некорректный номер телефона "{raw}": номер должен начинаться с 7 или 8')
        if digits not in seen:
            seen.add(digits)
            normalized.append(digits)
    return normalized


class CreateProjectItem(BaseModel):
    name: str
    tag: str
    collectionSource: CollectionSource
    dataSourceCode: DataSourceCode
    dataLimit: int
    status: ProjectMutableStatus
    regionMode: Optional[Literal['include','exclude']] = None
    regions: List[str] = []
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    days: List[Day]

    _normalize_phones = field_validator("phones")(_normalize_project_phones)


class CreateProjectsPayload(BaseModel):
    items: List[CreateProjectItem]


class ProjectUpdate(BaseModel):
    name: str
    tag: str
    status: ProjectMutableStatus
    dataLimit: int
    regionMode: Optional[Literal['include','exclude']] = None
    regions: List[str] = []
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    days: List[Day]

    _normalize_phones = field_validator("phones")(_normalize_project_phones)


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


class UpdateProjectOut(BaseModel):
    project: ProjectOut
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
    inn: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[UserRole] = None
    ownerAgentId: Optional[int] = None
    isDisabled: Optional[bool] = None
    autoLimitControlEnabled: Optional[bool] = None
    telegramNotificationsChatId: Optional[str] = None
    telegramAutoPauseEnabled: Optional[bool] = None
    uniqueProjectNamesEnabled: Optional[bool] = None


class SelfProfileOut(BaseModel):
    """Краткий профиль текущего пользователя."""
    id: int
    login: str
    name: Optional[str] = None
    role: UserRole = 'client'
    ownerAgentId: Optional[int] = None
    viaImpersonation: bool = False
    impersonatorUserId: Optional[int] = None
    isDisabled: bool = False
    projectsMutationLocked: bool = False
    projectsMutationLockedAt: Optional[str] = None
    projectsMutationLockedBy: Optional[int] = None
    projectsMutationLockReason: Optional[str] = None
    autoLimitControlEnabled: bool = False
    telegramNotificationsChatId: Optional[str] = None
    telegramAutoPauseEnabled: bool = False
    uniqueProjectNamesEnabled: bool = False


class ClientProfileOut(BaseModel):
    name: str
    inn: Optional[str] = None
    phone: Optional[str] = None
    contact: Optional[str] = None
    internalClientId: Optional[str] = None
    tableUrl: Optional[str] = None
    pixelTableUrl: Optional[str] = None
    workStatus: ClientWorkStatus = 'В работе'

    class Config:
        from_attributes = True


class AdminClientCreateIn(BaseModel):
    name: str
    inn: Optional[str] = None
    phone: Optional[str] = None
    contact: Optional[str] = None
    login: Optional[str] = None
    password: Optional[str] = None
    autoLimitControlEnabled: bool = False
    telegramNotificationsChatId: Optional[str] = None
    telegramAutoPauseEnabled: bool = False
    uniqueProjectNamesEnabled: bool = False
    internalClientId: Optional[str] = None
    tableUrl: Optional[str] = None
    pixelTableUrl: Optional[str] = None
    ownerAgentId: Optional[int] = None


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
    autoLimitControlEnabled: Optional[bool] = None
    telegramNotificationsChatId: Optional[str] = None
    telegramAutoPauseEnabled: Optional[bool] = None
    uniqueProjectNamesEnabled: Optional[bool] = None
    internalClientId: Optional[str] = None
    tableUrl: Optional[str] = None
    pixelTableUrl: Optional[str] = None
    ownerAgentId: Optional[int] = None


class AdminClientUpdateOut(BaseModel):
    user: UserInfo
    profile: ClientProfileOut
    login: str
    password: Optional[str] = None
    telegramTestStatus: Optional[Literal['queued', 'failed']] = None
    telegramTestNotificationId: Optional[int] = None


class AdminClientWorkStatusUpdateIn(BaseModel):
    workStatus: ClientWorkStatus


class AdminClientWorkStatusUpdateOut(BaseModel):
    clientId: int
    workStatus: ClientWorkStatus


class AdminProjectOut(ProjectOut):
    """Проект с информацией о владельце (для админа)."""
    user: UserInfo


class AdminProjectListOut(BaseModel):
    items: List[AdminProjectOut]
    total: int


class AdminUpdateProjectOut(BaseModel):
    project: AdminProjectOut
    warning: Optional[str] = None


class AdminProjectUpdate(BaseModel):
    """Обновление проекта админом (включая delivery_status)."""
    name: str
    tag: str
    status: ProjectMutableStatus
    deliveryStatus: DeliveryStatus
    dataLimit: int
    regionMode: Optional[Literal['include','exclude']] = None
    regions: List[str] = []
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    days: List[Day]

    _normalize_phones = field_validator("phones")(_normalize_project_phones)


class ClientErrorIn(BaseModel):
    message: str = Field(..., description="Сообщение ошибки")
    stack: Optional[str] = Field(None, description="Стек ошибки")
    url: Optional[str] = Field(None, description="URL страницы")
    userAgent: Optional[str] = Field(None, description="User-Agent браузера")
    level: Optional[Literal['error','warn','info']] = 'error'
    time: Optional[str] = None



class LeadOut(BaseModel):
    ext_id: str
    lk_id: str
    project_id: Optional[int] = None
    project_name: Optional[str] = None
    created_at: str
    imported_at: str
    phone: str
    utm_campaign: Optional[str] = None
    source: Optional[str] = None
    lead_source: LeadSource = "provider"
    pixel_url: Optional[str] = None
    collection_source: Optional[str] = None

    class Config:
        from_attributes = True


class LeadsListOut(BaseModel):
    items: List[LeadOut]
    total: int


# -------- Админские схемы для лидов --------
class AdminLeadOut(BaseModel):
    ext_id: str
    lk_id: str
    project_id: Optional[int] = None
    created_at: str
    imported_at: str
    phone: str
    utm_campaign: Optional[str] = None
    source: Optional[str] = None
    lead_source: LeadSource = "provider"
    pixel_url: Optional[str] = None
    collection_source: Optional[str] = None
    project_name: Optional[str] = None
    user: UserInfo

    class Config:
        from_attributes = True


class AdminLeadsListOut(BaseModel):
    items: List[AdminLeadOut]
    total: int


class AdminProviderLeadsImportPreviewSampleOut(BaseModel):
    xlsxRowNumber: Optional[int] = None
    vid: Optional[str] = None
    projectName: Optional[str] = None
    phone: Optional[str] = None
    subdomain: Optional[str] = None
    note: str


class AdminProviderLeadsImportPreviewOut(BaseModel):
    previewId: str
    fileName: str
    totalRows: int
    validRows: int
    rowsWithErrors: int
    duplicatesInFile: int
    duplicatesInDb: int
    newRows: int
    readyToImport: int
    matchedProjects: int
    notFoundProjects: int
    ambiguousProjects: int
    errorsBreakdown: Dict[str, int]
    samples: Dict[str, List[AdminProviderLeadsImportPreviewSampleOut]]


class AdminProviderLeadsImportCommitIn(BaseModel):
    previewId: str


class AdminProviderLeadsImportCommitOut(BaseModel):
    previewId: str
    fileName: str
    insertedRows: int
    skippedDuplicatesInDb: int


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


# -------- История активности клиента --------
ActivityEntity = Literal['project', 'blacklist', 'balance', 'report']
ActorMode = Literal['client', 'admin', 'admin_impersonation']
OperationOutcome = Literal['success', 'failed']
HistoryEventSource = Literal['audit', 'project_operation']


class ActivityEventOut(BaseModel):
    eventId: str
    sourceId: int
    entity: ActivityEntity
    action: str
    createdAt: str
    client: Optional[UserInfo] = None
    actor: Optional[UserInfo] = None
    description: str
    outcome: OperationOutcome = 'success'
    errorMessage: Optional[str] = None
    status: Literal['pending', 'done'] = 'done'
    projectId: Optional[int] = None
    projectName: Optional[str] = None
    periodFrom: Optional[str] = None
    periodTo: Optional[str] = None


class ActivityEventListOut(BaseModel):
    items: List[ActivityEventOut]
    total: int


# -------- История изменений проектов --------
class ProjectHistoryItem(BaseModel):
    id: int
    eventId: str
    action: Literal['create', 'update', 'delete']
    createdAt: str
    description: str
    outcome: OperationOutcome = 'success'
    errorMessage: Optional[str] = None
    actor: Optional[UserInfo] = None
    actorMode: Optional[ActorMode] = None


class AdminProjectHistoryItem(BaseModel):
    id: int
    eventId: str
    action: Literal['create', 'update', 'delete']
    createdAt: str
    description: str
    outcome: OperationOutcome = 'success'
    errorMessage: Optional[str] = None
    user: Optional[UserInfo] = None
    actorMode: Optional[ActorMode] = None
    status: Literal['pending', 'done'] = 'pending'
    projectSnapshot: Optional[dict] = None


class AdminProjectHistoryListOut(BaseModel):
    items: List[AdminProjectHistoryItem]
    total: int


class HistoryEventDetailOut(BaseModel):
    eventId: str
    source: HistoryEventSource
    sourceId: int
    action: Literal['create', 'update', 'delete']
    createdAt: str
    description: str
    outcome: OperationOutcome = 'success'
    errorMessage: Optional[str] = None
    projectId: Optional[int] = None
    projectName: Optional[str] = None
    actor: Optional[UserInfo] = None
    actorMode: Optional[ActorMode] = None
    projectSnapshot: Optional[dict] = None
    beforeSnapshot: Optional[dict] = None
    changedFields: Optional[List[str]] = None


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
    actor: Optional[UserInfo] = None
    actorMode: Optional[ActorMode] = None


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
class AdminClientSummarySeriesPoint(BaseModel):
    date: str
    value: int


class AdminClientSummaryItem(BaseModel):
    user: UserInfo
    profile: Optional[ClientProfileOut] = None
    ownerType: Literal['admin', 'agent'] = 'admin'
    ownerUser: Optional[UserInfo] = None
    projectCount: int
    totalLimit: int
    usedTotal: int
    usedPeriod: int
    usedPeriodBySource: Dict[str, int] = Field(default_factory=dict)
    averageWorkday7: float = 0.0
    averageWorkday3: float = 0.0
    averageWorkday7BySource: Dict[str, float] = Field(default_factory=dict)
    averageWorkday3BySource: Dict[str, float] = Field(default_factory=dict)
    leadsDaily30BySource: Dict[str, List[AdminClientSummarySeriesPoint]] = Field(default_factory=dict)
    remaining: int
    pendingChanges: int = 0
    pendingCreates: int = 0
    # Новые поля по номерам
    numbersCredited: int | None = None
    numbersDebited: int | None = None
    numbersBalance: int | None = None
    numbersUsed: int | None = None
    numbersUsedPeriod: int | None = None
    tariffAmount: int | None = None
    autoLimitControlEnabled: bool = False
    dataCollectionStatus: ClientDataCollectionStatus = 'Нет проектов'
    financeStatus: Optional[ClientFinanceStatus] = None
    workStatus: ClientWorkStatus = 'В работе'


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


# -------- Админский дашборд --------
DashboardRiskLevel = Literal['warning', 'risk', 'critical', 'debt']


class AdminDashboardMetric(BaseModel):
    value: int
    label: Optional[str] = None


class AdminDashboardSummaryOut(BaseModel):
    clients: int
    projects: int
    activeProjects: int
    pausedProjects: int
    operatorBlockedProjects: int
    totalRemaining: int
    leadsPeriod: int
    leadsToday: int
    leadsYesterday: int
    leads7Days: int
    leads30Days: int
    averageWorkday7: float = 0.0
    averageWorkday3: float = 0.0
    unlinkedLeads: int
    operationErrors: int


class AdminDashboardAttentionClientOut(BaseModel):
    clientId: int
    clientName: str
    clientLogin: str
    ownerType: Literal['admin', 'agent'] = 'admin'
    ownerName: Optional[str] = None
    remaining: int
    tariffAmount: Optional[int] = None
    signal1: int
    signal2: int
    signal3: Optional[int] = None
    level: DashboardRiskLevel
    activeProjects: int
    dailySpend: int
    lastTariffAt: Optional[str] = None


class AdminDashboardAttentionProjectOut(BaseModel):
    projectId: int
    projectName: str
    clientId: Optional[int] = None
    clientName: Optional[str] = None
    source: str
    detectedAt: Optional[str] = None


class AdminDashboardUnlinkedLeadsOut(BaseModel):
    total: int
    ambiguous: int
    notFound: int
    unknown: int


class AdminDashboardOperationErrorsOut(BaseModel):
    total: int
    items: List[Dict[str, Any]] = Field(default_factory=list)


class AdminDashboardAttentionOut(BaseModel):
    criticalClients: List[AdminDashboardAttentionClientOut]
    riskClients: List[AdminDashboardAttentionClientOut]
    warningClients: List[AdminDashboardAttentionClientOut]
    operatorBlockedProjects: List[AdminDashboardAttentionProjectOut]
    unlinkedLeads: AdminDashboardUnlinkedLeadsOut
    operationErrors: AdminDashboardOperationErrorsOut


class AdminDashboardSeriesPointOut(BaseModel):
    date: str
    value: int


class AdminDashboardBreakdownItemOut(BaseModel):
    key: str
    label: str
    value: int


class AdminDashboardChartsOut(BaseModel):
    leadsDaily: List[AdminDashboardSeriesPointOut]
    sourceBreakdown: List[AdminDashboardBreakdownItemOut]
    projectStatuses: List[AdminDashboardBreakdownItemOut]


class ProjectChartOut(BaseModel):
    projectId: int
    projectName: str
    fromDate: str
    toDate: str
    total: int
    averageDaily: float
    leadsDaily: List[AdminDashboardSeriesPointOut]
    sourceBreakdown: List[AdminDashboardBreakdownItemOut]


class AdminDashboardClientRankingItemOut(BaseModel):
    clientId: int
    clientName: str
    ownerName: Optional[str] = None
    value: int
    activeProjects: int = 0


class AdminDashboardRankingsOut(BaseModel):
    topClientsByLeads: List[AdminDashboardClientRankingItemOut]
    topClientsByActiveProjects: List[AdminDashboardClientRankingItemOut]


class AdminDashboardOut(BaseModel):
    summary: AdminDashboardSummaryOut
    attention: AdminDashboardAttentionOut
    charts: AdminDashboardChartsOut
    rankings: AdminDashboardRankingsOut


# -------- Клиентский дашборд --------
DashboardSpendBasis = Literal['7d', '30d']


class ClientDashboardSummaryOut(BaseModel):
    leadsPeriod: int
    leadsToday: int
    leads7Days: int
    leads30Days: int
    remaining: int
    activeProjects: int
    pausedProjects: int
    operatorBlockedProjects: int


class ClientDashboardBalanceOut(BaseModel):
    remaining: int
    averageDailySpend: float
    averageDailySpendBasis: Optional[DashboardSpendBasis] = None
    estimatedDaysLeft: Optional[int] = None


class ClientDashboardAttentionProjectOut(BaseModel):
    projectId: int
    projectName: str
    status: ProjectStatus
    source: str
    reason: Literal['operator_blocked', 'paused', 'no_data_7d']
    reasonLabel: str


class ClientDashboardChartsOut(BaseModel):
    leadsDaily: List[AdminDashboardSeriesPointOut]
    projectStatuses: List[AdminDashboardBreakdownItemOut]


class ClientDashboardProjectRankingItemOut(BaseModel):
    projectId: int
    projectName: str
    status: ProjectStatus
    source: str
    value: int


class ClientDashboardAttentionOut(BaseModel):
    projects: List[ClientDashboardAttentionProjectOut]


class ClientDashboardRankingsOut(BaseModel):
    topProjectsByLeads: List[ClientDashboardProjectRankingItemOut]


class ClientDashboardOut(BaseModel):
    summary: ClientDashboardSummaryOut
    balance: ClientDashboardBalanceOut
    charts: ClientDashboardChartsOut
    attention: ClientDashboardAttentionOut
    rankings: ClientDashboardRankingsOut
    recentEvents: List[ActivityEventOut]


class AdminAgentCreateIn(BaseModel):
    name: str
    inn: str
    phone: str
    login: Optional[str] = None
    password: Optional[str] = None


class AdminAgentUpdateIn(BaseModel):
    name: Optional[str] = None
    inn: Optional[str] = None
    phone: Optional[str] = None
    login: Optional[str] = None
    password: Optional[str] = None
    isDisabled: Optional[bool] = None


class AdminAgentCreateOut(BaseModel):
    user: UserInfo
    login: str
    password: str


class AdminAgentUpdateOut(BaseModel):
    user: UserInfo
    login: str
    password: Optional[str] = None


class AdminAgentSummaryItem(BaseModel):
    user: UserInfo
    clientCount: int
    credited: int
    debited: int
    balance: int
    createdAt: str


class AdminAgentsListOut(BaseModel):
    items: List[AdminAgentSummaryItem]
    total: int


class ClientOwnerTransferIn(BaseModel):
    ownerType: Literal['admin', 'agent']
    agentId: Optional[int] = None


class ClientOwnerTransferOut(BaseModel):
    client: UserInfo
    ownerType: Literal['admin', 'agent']
    ownerUser: Optional[UserInfo] = None
    transferredBalance: int


# -------- Админ: сбор данных (пауза всех проектов клиента) --------
class AdminCollectionProjectItem(BaseModel):
    id: int
    name: str
    status: ProjectStatus


class AdminClientCollectionStateOut(BaseModel):
    clientId: int
    dataCollectionStatus: Literal['Активен', 'На паузе']
    action: Literal['pause', 'resume']
    actionLabel: str
    pauseCandidates: int = 0
    resumeCandidates: int = 0
    actionEnabled: bool = True
    actionDisabledReason: Optional[str] = None
    projectsMutationLocked: bool = False
    projectsMutationLockedAt: Optional[str] = None
    projectsMutationLockedBy: Optional[int] = None
    projectsMutationLockReason: Optional[str] = None
    snapshotProjects: List[AdminCollectionProjectItem] = Field(default_factory=list)


class AdminClientCollectionActionOut(BaseModel):
    state: AdminClientCollectionStateOut
    message: str
    pausedCount: int = 0
    resumedCount: int = 0
    skippedCount: int = 0
    failedCount: int = 0
    errors: List[str] = Field(default_factory=list)


class OperatorBlockProjectOut(BaseModel):
    id: int
    name: str
    clientName: Optional[str] = None
    providerProjectId: Optional[str] = None


class OperatorBlockCheckOut(BaseModel):
    checked: int
    blocked: int
    skipped: int = 0
    blockedProjects: List[OperatorBlockProjectOut] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


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
    periodFrom: Optional[str] = None
    periodTo: Optional[str] = None


class ClientBalanceOpsListOut(BaseModel):
    items: List[BalanceOperationOut]
    total: int


# -------- Тарифы клиента --------
TariffOpType = Literal['credit', 'debit']


class ClientTariffOut(BaseModel):
    id: int
    clientId: int
    baseAmount: int
    currentAmount: int
    comment: Optional[str] = None
    signal1: Optional[int] = None
    signal2: Optional[int] = None
    signal3: Optional[int] = None
    createdAt: str
    updatedAt: str
    createdBy: UserInfo


class ClientTariffCreateIn(BaseModel):
    amount: int
    comment: Optional[str] = None
    signal1: int
    signal2: int
    signal3: Optional[int] = None


class ClientTariffUpdateIn(BaseModel):
    amount: int
    comment: Optional[str] = None
    signal1: int
    signal2: int
    signal3: Optional[int] = None


class ClientTariffListOut(BaseModel):
    items: List[ClientTariffOut]
    total: int


class ClientTariffOperationOut(BaseModel):
    id: int
    tariffId: int
    amount: int
    type: TariffOpType
    comment: str
    createdAt: str
    createdBy: UserInfo


class ClientTariffOperationCreateIn(BaseModel):
    amount: int
    type: TariffOpType
    comment: str


class ClientTariffOperationsListOut(BaseModel):
    items: List[ClientTariffOperationOut]
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
    clientId: Optional[int] = None


class ClientCreateReportIn(BaseModel):
    fromDate: str
    toDate: str
    projectIds: Optional[List[int]] = None
    format: str = "csv"


# -------- Поддержка (сообщение в Telegram) --------
class SupportMessageIn(BaseModel):
    phone: str
    text: str


TelegramNotificationStatus = Literal['pending', 'processing', 'sent', 'failed']


class TelegramNotificationClaimIn(BaseModel):
    workerId: str
    limit: int = Field(50, ge=1, le=100)


class TelegramNotificationClaimItemOut(BaseModel):
    id: int
    kind: str
    chatId: str
    text: str
    parseMode: Optional[str] = "HTML"
    metadata: Optional[Dict[str, Any]] = None


class TelegramNotificationClaimOut(BaseModel):
    items: List[TelegramNotificationClaimItemOut]


class TelegramNotificationResultIn(BaseModel):
    status: Literal['sent', 'failed']
    telegramMessageId: Optional[str] = None
    error: Optional[str] = None


class TelegramNotificationResultOut(BaseModel):
    ok: bool
    id: int
    status: TelegramNotificationStatus


class QueuedNotificationOut(BaseModel):
    ok: bool = True
    status: Literal['queued'] = 'queued'
    notificationId: Optional[int] = None
