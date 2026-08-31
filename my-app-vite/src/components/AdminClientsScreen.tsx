// Экран «Клиенты» для админа: список клиентов с агрегированной статистикой по проектам
// Логика максимально простая и прозрачная, без лишних сущностей.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  fetchAdminClientsSummary,
  fetchAdminChangesSummary,
  fetchAdminUsers,
  impersonateClient,
  fetchAdminClientTariffs,
  fetchAdminTariffOps,
  createAdminClientTariff,
  createAdminTariffOp,
  updateAdminTariff,
  transferAdminClientOwner,
  updateAdminClient,
  updateAdminClientWorkStatus,
  fetchAdminClientCollectionState,
  pauseAdminClientProjects,
  resumeAdminClientProjects,
  type ProjectOperation,
  type AdminClientSummaryItem,
  type AdminDashboardSeriesPoint,
  type AdminClientChangesSummaryListOut,
  type AdminClientCollectionState,
  type ClientProfile,
  type ClientWorkStatus,
  type ClientDataCollectionStatus,
  type ClientFinanceStatus,
} from '../api';
import {
  getProjectOperationStatusLabel,
  getProjectOperationUserMessage,
  isProjectOperationActive,
  useProjectOperation,
} from '../utils/useProjectOperation';
import DateRangeFilter from './DateRangeFilter';
import DateRangeCompact from './DateRangeCompact';
import AdminCreateClientModal from './AdminCreateClientModal';
import AdminClientCardModal from './AdminClientCardModal';
import TariffManagerModal from './TariffManagerModal';
import DashboardDailyChart from './DashboardDailyChart';
import {
  RAW_SOURCE_CODES,
  formatProjectNameForDisplay,
  getSourceCodeFilterOptions,
  toDisplaySourceCode,
} from '../utils/sourceCodeDisplay';

type ClientProfileWithContact = ClientProfile & {
  contact?: string | null;
};

// Важно: начиная с разделения логики «Клиенты» / «Проекты»,
// сам экран «Клиенты» НЕ занимается обработкой проектов и изменений.
// Он только показывает агрегированный дашборд по клиентам и
// даёт быстрый переход во вкладку «Проекты».
//
// Для этого сюда прокидываются коллбеки onOpenClientProjects / onOpenClientChanges
// из корневого лэйаута (App.tsx).

export type AdminClientsScreenProps = {
  managerRole?: 'admin' | 'agent';
  onOpenClientProjects?: (clientId: number, clientName: string) => void;
  onOpenClientChanges?: (clientId: number, clientName: string) => void;
  onOpenClientBlacklistChanges?: (clientId: number, clientName: string) => void;
  onOpenClientBalance?: (clientId: number, clientName: string, action: 'tariff') => void;
};

type ClientRow = {
  id: number;
  name: string;
  login: string;
  ownerType: 'admin' | 'agent';
  ownerUser?: { id: number; name: string; login: string } | null;
  projectCount: number;
  dataCollectionStatus: ClientDataCollectionStatus;
  financeStatus?: ClientFinanceStatus | null;
  workStatus: ClientWorkStatus;
  tariffAmount?: number | null;
  remaining: number;      // Остаток по лимиту
  totalVolume: number;    // Использовано за период
  totalLimit: number;
  usedTotal: number;
  usedPeriodBySource: Record<string, number>;
  averageWorkday7: number;
  averageWorkday3: number;
  averageWorkday7BySource: Record<string, number>;
  averageWorkday3BySource: Record<string, number>;
  leadsDaily30BySource: Record<string, AdminDashboardSeriesPoint[]>;
  pendingChanges: number;
  pendingCreates: number;
  pendingBlacklistAdds: number;
  pendingBlacklistDeletes: number;
  autoLimitControlEnabled: boolean;
  telegramNotificationsChatId?: string | null;
  telegramAutoPauseEnabled: boolean;
  uniqueProjectNamesEnabled: boolean;
  internalClientId?: string | null;
  tableUrl?: string | null;
  pixelTableUrl?: string | null;
  inn?: string | null;
  phone?: string | null;
  contact?: string | null;
};

type DateRange = { from: string; to: string };

const ARCHIVE_EXPANDED_STORAGE_KEY = 'admin_clients_archive_expanded';

const WORK_STATUSES: ClientWorkStatus[] = [
  'В работе',
  'Ждём оплату',
  'Ждём данные',
  'На согласовании',
  'Пауза по клиенту',
  'Неактивен',
];

// Вспомогательный хелпер: вернуть диапазон "сегодня"
function getTodayRange(): DateRange {
  const today = new Date().toISOString().slice(0, 10);
  return { from: today, to: today };
}

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function formatOwnerLabel(row: ClientRow): string {
  if (row.ownerType === 'agent' && row.ownerUser) {
    return `${row.ownerUser.name} (агент)`;
  }
  return 'Админ';
}

function sumSourceRecord(record: Record<string, number> | undefined, sources: readonly string[]): number {
  return sources.reduce((sum, source) => sum + Number(record?.[source] ?? 0), 0);
}

function formatSummaryNumber(value: number): string {
  if (!Number.isFinite(value)) return '0';
  const rounded = Math.round(value * 100) / 100;
  return rounded.toLocaleString('ru-RU', {
    maximumFractionDigits: Number.isInteger(rounded) ? 0 : 2,
  });
}

function sumDailySeriesBySource(
  record: Record<string, AdminDashboardSeriesPoint[]> | undefined,
  sources: readonly string[],
): AdminDashboardSeriesPoint[] {
  const firstSeries = RAW_SOURCE_CODES.map((source) => record?.[source]).find((series) => Array.isArray(series) && series.length) ?? [];
  return firstSeries.map((point, index) => ({
    date: point.date,
    value: sources.reduce((sum, source) => sum + Number(record?.[source]?.[index]?.value ?? 0), 0),
  }));
}

function getCollectionBadgeClass(status: ClientDataCollectionStatus): string {
  if (status === 'Сбор активен') return 'badge badge--green';
  if (status === 'Нет проектов') return 'badge badge--info';
  return 'badge badge--gray';
}

function getFinanceBadgeClass(status: ClientFinanceStatus): string {
  if (status === 'Долг') return 'badge badge--red';
  if (status === 'Дожим 3') return 'badge badge--red-orange';
  if (status === 'Дожим 2') return 'badge badge--orange';
  return 'badge badge--soft-orange';
}

function getWorkStatusSelectClass(status: ClientWorkStatus): string {
  if (status === 'В работе') return 'client-work-select client-work-select--green';
  if (status === 'Ждём оплату') return 'client-work-select client-work-select--orange';
  if (status === 'Ждём данные') return 'client-work-select client-work-select--blue';
  if (status === 'На согласовании') return 'client-work-select client-work-select--violet';
  return 'client-work-select client-work-select--gray';
}

function AdminClientsScreen({
  managerRole = 'admin',
  onOpenClientProjects,
  onOpenClientChanges,
  onOpenClientBlacklistChanges,
  onOpenClientBalance,
}: AdminClientsScreenProps) {
  const isAgentManager = managerRole === 'agent';
  const env = import.meta.env as Record<string, unknown>;
  const [baseClients, setBaseClients] = useState<ClientRow[]>([]);
  const [agents, setAgents] = useState<Array<{ id: number; name: string; login: string; isDisabled: boolean }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [activePage, setActivePage] = useState(1);
  const [archivePage, setArchivePage] = useState(1);
  const [archiveExpanded, setArchiveExpanded] = useState(() => {
    try {
      return localStorage.getItem(ARCHIVE_EXPANDED_STORAGE_KEY) !== '0';
    } catch {
      return true;
    }
  });
  const [pageSize, setPageSize] = useState(100);
  const [selectedClientId, setSelectedClientId] = useState<number | null>(null);
  const pendingCreatedClientIdRef = useRef<number | null>(null);
  const [selectedSummarySources, setSelectedSummarySources] = useState<string[]>(() => [...RAW_SOURCE_CODES]);
  const [range, setRange] = useState<DateRange>(() => getTodayRange());
  const [createOpen, setCreateOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [cardClientId, setCardClientId] = useState<number | null>(null);
  const [openingClientCabinetId, setOpeningClientCabinetId] = useState<number | null>(null);
  const [savingWorkStatusClientId, setSavingWorkStatusClientId] = useState<number | null>(null);
  const [collectionState, setCollectionState] = useState<AdminClientCollectionState | null>(null);
  const [collectionLoading, setCollectionLoading] = useState(false);
  const [collectionActionLoading, setCollectionActionLoading] = useState(false);
  const [collectionRunInfo, setCollectionRunInfo] = useState<{ mode: 'pause' | 'resume'; total: number } | null>(null);
  const [collectionLastInfo, setCollectionLastInfo] = useState<string | null>(null);
  const [pauseSnapshotExpanded, setPauseSnapshotExpanded] = useState(false);
  const [ownerTarget, setOwnerTarget] = useState<string>('admin');
  const [tariffModalState, setTariffModalState] = useState<{
    clientId: number;
    clientName: string;
    mode: 'list' | 'create';
  } | null>(null);
  const [cardClientData, setCardClientData] = useState<{
    name: string;
    inn?: string | null;
    phone?: string | null;
    contact?: string | null;
    telegramNotificationsChatId?: string | null;
    telegramAutoPauseEnabled: boolean;
    uniqueProjectNamesEnabled: boolean;
    internalClientId?: string | null;
    tableUrl?: string | null;
    pixelTableUrl?: string | null;
    login: string;
  } | null>(null);

  function handleProjectOperationTerminal(operation: ProjectOperation) {
    const completed = operation.completedCount || operation.successCount + operation.failedCount;
    setCollectionActionLoading(false);
    setCollectionRunInfo(null);
    setCollectionLastInfo(
      `Выполнено: ${completed}/${operation.totalCount}. Успешно: ${operation.successCount}. Ошибок: ${operation.failedCount}.`,
    );
    setRefreshKey((value) => value + 1);
    window.dispatchEvent(new CustomEvent('app-toast', {
      detail: `${getProjectOperationUserMessage(operation, { isAdmin: true })}\nСтатус: ${getProjectOperationStatusLabel(operation.status)}.\nВыполнено: ${completed}/${operation.totalCount}. Успешно: ${operation.successCount}. Ошибок: ${operation.failedCount}.`,
    }));
  }

  const projectOperationTracker = useProjectOperation({
    clientId: selectedClientId ?? undefined,
    enabled: !isAgentManager && selectedClientId != null,
    onTerminal: handleProjectOperationTerminal,
  });
  const projectOperation = projectOperationTracker.operation;
  const operationActive = isProjectOperationActive(projectOperation);
  const collectionBusy = collectionActionLoading || operationActive;

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const [summary, combinedSummary, users] = await Promise.all([
          fetchAdminClientsSummary({ fromDate: range.from, toDate: range.to }),
          fetchAdminChangesSummary({ actions: ['create', 'update', 'delete', 'blacklist_add', 'blacklist_delete'] }).catch(
            () => ({ items: [] } as AdminClientChangesSummaryListOut),
          ),
          fetchAdminUsers({ includeAgents: true }).catch(() => []),
        ]);
        setAgents(
          users
            .filter((user) => user.role === 'agent')
            .map((user) => ({
              id: user.id,
              name: user.name || user.login,
              login: user.login,
              isDisabled: Boolean(user.isDisabled),
            })),
        );
        const pendingMap: Record<number, number> = {};
        const createsMap: Record<number, number> = {};
        const blAddsMap: Record<number, number> = {};
        const blDeletesMap: Record<number, number> = {};
        combinedSummary.items.forEach((i) => {
          pendingMap[i.user.id] = i.pendingChanges ?? 0;
          createsMap[i.user.id] = i.pendingCreates ?? 0;
          blAddsMap[i.user.id] = i.pendingBlacklistAdds ?? 0;
          blDeletesMap[i.user.id] = i.pendingBlacklistDeletes ?? 0;
        });
        const rows: ClientRow[] = summary.items.map((it: AdminClientSummaryItem) => {
          const profile: ClientProfileWithContact | null | undefined = it.profile;
          const displayName = profile?.name?.trim() || it.user.name?.trim() || it.user.login;
          const row: ClientRow = {
            id: it.user.id,
            name: displayName,
            login: it.user.login,
            ownerType: it.ownerType,
            ownerUser: it.ownerUser
              ? {
                  id: it.ownerUser.id,
                  name: it.ownerUser.name || it.ownerUser.login,
                  login: it.ownerUser.login,
                }
              : null,
            projectCount: it.projectCount,
            dataCollectionStatus: it.dataCollectionStatus ?? 'Нет проектов',
            financeStatus: it.financeStatus ?? null,
            workStatus: it.workStatus ?? profile?.workStatus ?? 'В работе',
            tariffAmount: it.tariffAmount ?? null,
            remaining: it.remaining,
            totalVolume: it.usedPeriod,
            totalLimit: it.totalLimit,
            usedTotal: it.usedTotal,
            usedPeriodBySource: it.usedPeriodBySource ?? {},
            averageWorkday7: it.averageWorkday7 ?? 0,
            averageWorkday3: it.averageWorkday3 ?? 0,
            averageWorkday7BySource: it.averageWorkday7BySource ?? {},
            averageWorkday3BySource: it.averageWorkday3BySource ?? {},
            leadsDaily30BySource: it.leadsDaily30BySource ?? {},
            pendingChanges: pendingMap[it.user.id] ?? it.pendingChanges ?? 0,
            pendingCreates: createsMap[it.user.id] ?? it.pendingCreates ?? 0,
            pendingBlacklistAdds: blAddsMap[it.user.id] ?? 0,
            pendingBlacklistDeletes: blDeletesMap[it.user.id] ?? 0,
            autoLimitControlEnabled: Boolean(it.autoLimitControlEnabled),
            telegramNotificationsChatId: it.user.telegramNotificationsChatId ?? null,
            telegramAutoPauseEnabled: Boolean(it.user.telegramAutoPauseEnabled),
            uniqueProjectNamesEnabled: Boolean(it.user.uniqueProjectNamesEnabled),
            internalClientId: profile?.internalClientId ?? null,
            tableUrl: profile?.tableUrl ?? null,
            pixelTableUrl: profile?.pixelTableUrl ?? null,
            inn: profile?.inn,
            phone: profile?.phone,
            contact: profile?.contact,
          };
          return row;
        });
        setBaseClients(rows);
        const pendingCreatedClientId = pendingCreatedClientIdRef.current;
        if (pendingCreatedClientId != null && rows.some((row) => row.id === pendingCreatedClientId)) {
          pendingCreatedClientIdRef.current = null;
          setSelectedClientId(pendingCreatedClientId);
        }
        try {
          const focusIdRaw = localStorage.getItem('admin_clients_focus_id');
          const focusId = focusIdRaw ? Number(focusIdRaw) : null;
          if (pendingCreatedClientId == null && focusId && rows.some((row) => row.id === focusId)) {
            setSelectedClientId(focusId);
            localStorage.removeItem('admin_clients_focus_id');
          }
        } catch {
          /* ignore */
        }
        setPage(1);
        setActivePage(1);
        setArchivePage(1);
      } catch (err: unknown) {
        console.error(err);
        setError(getErrorMessage(err, 'Не удалось загрузить клиентов'));
      } finally {
        setLoading(false);
      }
    })();
  }, [range, refreshKey]);

  const clients = useMemo(() => {
    if (isAgentManager) return baseClients;
    return baseClients.filter((client) => client.ownerType === 'admin');
  }, [baseClients, isAgentManager]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return clients;
    return clients.filter((c) => {
      const nameHit = c.name.toLowerCase().includes(q);
      const idHit = String(c.id).includes(q);
      return nameHit || idHit;
    });
  }, [clients, search]);

  const activeClients = useMemo(
    () => (isAgentManager ? filtered : filtered.filter((client) => client.workStatus !== 'Неактивен')),
    [filtered, isAgentManager],
  );

  const archivedClients = useMemo(
    () => (isAgentManager ? [] : filtered.filter((client) => client.workStatus === 'Неактивен')),
    [filtered, isAgentManager],
  );

  function calculateTotals(rows: ClientRow[]) {
    let totalRemaining = 0;
    let totalVolume = 0;
    let totalProjects = 0;
    rows.forEach((c) => {
      totalRemaining += c.remaining;
      totalVolume += c.totalVolume;
      totalProjects += c.projectCount;
    });
    return { totalRemaining, totalVolume, totalProjects };
  }

  const totals = useMemo(() => calculateTotals(filtered), [filtered]);

  useEffect(() => {
    try {
      localStorage.setItem(ARCHIVE_EXPANDED_STORAGE_KEY, archiveExpanded ? '1' : '0');
    } catch {
      /* ignore */
    }
  }, [archiveExpanded]);

  const selectedClient = useMemo(
    () => (selectedClientId != null ? clients.find((c) => c.id === selectedClientId) ?? null : null),
    [clients, selectedClientId],
  );

  useEffect(() => {
    if (selectedClientId == null) return;
    if (clients.some((client) => client.id === selectedClientId)) return;
    if (pendingCreatedClientIdRef.current === selectedClientId) return;
    setSelectedClientId(clients[0]?.id ?? null);
  }, [clients, selectedClientId]);

  useEffect(() => {
    if (!selectedClient) {
      setOwnerTarget('admin');
      return;
    }
    if (selectedClient.ownerType === 'agent' && selectedClient.ownerUser?.id) {
      setOwnerTarget(`agent:${selectedClient.ownerUser.id}`);
      return;
    }
    setOwnerTarget('admin');
  }, [selectedClient]);

  // перезагрузка сводки при переходе обратно будет происходить через эффект range

  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const pageSafe = Math.min(Math.max(page, 1), totalPages);
  const start = (pageSafe - 1) * pageSize;
  const end = start + pageSize;
  const pageRows = filtered.slice(start, end);
  const selectedPendingTotal =
    selectedClient != null
      ? (selectedClient.pendingChanges ?? 0) +
        (selectedClient.pendingCreates ?? 0) +
        (selectedClient.pendingBlacklistAdds ?? 0) +
        (selectedClient.pendingBlacklistDeletes ?? 0)
      : 0;
  const selectedBlacklistTotal =
    selectedClient != null
      ? (selectedClient.pendingBlacklistAdds ?? 0) + (selectedClient.pendingBlacklistDeletes ?? 0)
      : 0;
  const selectedAccrued =
    selectedClient != null
      ? (clients.find((c) => c.id === selectedClient.id)?.remaining ?? 0) + selectedClient.usedTotal
      : 0;
  const summarySourceOptions = getSourceCodeFilterOptions(RAW_SOURCE_CODES);
  const selectedSummaryUsedPeriod = selectedClient
    ? sumSourceRecord(selectedClient.usedPeriodBySource, selectedSummarySources)
    : 0;
  const selectedSummaryAverage7 = selectedClient
    ? sumSourceRecord(selectedClient.averageWorkday7BySource, selectedSummarySources)
    : 0;
  const selectedSummaryAverage3 = selectedClient
    ? sumSourceRecord(selectedClient.averageWorkday3BySource, selectedSummarySources)
    : 0;
  const selectedSummaryDaily30 = selectedClient
    ? sumDailySeriesBySource(selectedClient.leadsDaily30BySource, selectedSummarySources)
    : [];

  function toggleSummarySource(source: string) {
    setSelectedSummarySources((prev) => {
      const next = prev.includes(source)
        ? prev.filter((item) => item !== source)
        : [...prev, source];
      return next.length ? next : [...RAW_SOURCE_CODES];
    });
  }

  useEffect(() => {
    if (isAgentManager) {
      setCollectionState(null);
      setCollectionLoading(false);
      return;
    }
    if (selectedClientId == null) {
      setCollectionState(null);
      setCollectionLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        setCollectionLoading(true);
        const state = await fetchAdminClientCollectionState(selectedClientId);
        if (!cancelled) setCollectionState(state);
      } catch (err: unknown) {
        if (!cancelled) {
          setCollectionState(null);
          window.dispatchEvent(
            new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось загрузить состояние сбора данных') }),
          );
        }
      } finally {
        if (!cancelled) setCollectionLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedClientId, refreshKey, isAgentManager]);

  useEffect(() => {
    setPauseSnapshotExpanded(false);
  }, [selectedClientId]);

  useEffect(() => {
    if (isAgentManager || selectedClientId == null) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setSelectedClientId(null);
      }
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isAgentManager, selectedClientId]);

  const clientCabinetBase =
    typeof env.VITE_CLIENT_PORTAL_URL === 'string' && env.VITE_CLIENT_PORTAL_URL
      ? (env.VITE_CLIENT_PORTAL_URL as string)
      : '/';

  function handleClientCreated(created: { user: { id: number } }) {
    pendingCreatedClientIdRef.current = created.user.id;
    setRefreshKey((x) => x + 1);
    setCreateOpen(false);
    setSelectedClientId(created.user.id);
  }

  async function handleOpenClientCabinet(clientId: number) {
    if (!clientId) return;
    setOpeningClientCabinetId(clientId);
    setError(null);
    try {
      const resp = await impersonateClient(clientId);
      try {
        localStorage.setItem('access_token', resp.access_token);
        sessionStorage.setItem('access_token', resp.access_token);
        const parts = [`access_token=${encodeURIComponent(resp.access_token)}`, 'path=/', 'samesite=lax'];
        if (window.location.protocol === 'https:') parts.push('secure');
        document.cookie = parts.join('; ');
      } catch {
        /* ignore */
      }
      const targetUrl = new URL(clientCabinetBase, window.location.origin).toString();
      window.location.href = targetUrl;
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось открыть ЛК клиента'));
      setOpeningClientCabinetId(null);
    }
  }

  async function handleToggleCollection() {
    if (!selectedClient) return;
    if (!collectionState) return;
    if (collectionBusy) return;

    const isPause = collectionState.action === 'pause';
    const confirmText = isPause
      ? 'Поставить на паузу все активные проекты клиента? При включении восстановятся только те, что были активны.'
      : 'Включить проекты ранее поставленные на паузу?';
    if (!window.confirm(confirmText)) return;

    const estimatedTotal = isPause
      ? (collectionState.pauseCandidates || 0)
      : (collectionState.resumeCandidates || 0);
    setCollectionRunInfo({ mode: isPause ? 'pause' : 'resume', total: estimatedTotal });
    setCollectionLastInfo(null);
    setCollectionActionLoading(true);
    try {
      const resp = isPause
        ? await pauseAdminClientProjects(selectedClient.id)
        : await resumeAdminClientProjects(selectedClient.id);
      projectOperationTracker.start(resp.operation);
      window.dispatchEvent(new CustomEvent('app-toast', {
        detail: 'Операция сохранена и продолжится автоматически. Можно закрыть вкладку.',
      }));
    } catch (err: unknown) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось изменить режим сбора данных') }));
    } finally {
      // После постановки в очередь worker продолжает операцию независимо от браузера.
      if (!projectOperationTracker.operation || !isProjectOperationActive(projectOperationTracker.operation)) {
        setCollectionActionLoading(false);
        setCollectionRunInfo(null);
      }
    }
  }

  async function handleToggleAutoLimitControl() {
    if (isAgentManager) return;
    if (!selectedClient) return;
    const nextEnabled = !selectedClient.autoLimitControlEnabled;
    const confirmText = nextEnabled
      ? 'Включить авто-контроль лимитов для этого клиента?'
      : 'Выключить авто-контроль лимитов для этого клиента? После этого управление будет полностью ручным.';
    if (!window.confirm(confirmText)) return;
    try {
      await updateAdminClient(selectedClient.id, { autoLimitControlEnabled: nextEnabled });
      setBaseClients((prev) =>
        prev.map((row) =>
          row.id === selectedClient.id ? { ...row, autoLimitControlEnabled: nextEnabled } : row,
        ),
      );
      window.dispatchEvent(
        new CustomEvent('app-toast', {
          detail: nextEnabled
            ? 'Авто-контроль лимитов включён.'
            : 'Авто-контроль лимитов выключен. Управление проектами полностью ручное.',
        }),
      );
      setRefreshKey((x) => x + 1);
    } catch (err: unknown) {
      window.dispatchEvent(
        new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось изменить режим авто-контроля лимитов') }),
      );
    }
  }

  async function handleTransferOwner() {
    if (!selectedClient || isAgentManager) return;
    try {
      const payload =
        ownerTarget === 'admin'
          ? { ownerType: 'admin' as const }
          : { ownerType: 'agent' as const, agentId: Number(ownerTarget.split(':')[1]) };
      await transferAdminClientOwner(selectedClient.id, payload);
      window.dispatchEvent(new CustomEvent('app-toast', { detail: 'Клиент успешно переназначен' }));
      setRefreshKey((x) => x + 1);
    } catch (err: unknown) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось переназначить клиента') }));
    }
  }

  async function handleWorkStatusChange(clientId: number, nextStatus: ClientWorkStatus) {
    const prevRow = baseClients.find((row) => row.id === clientId);
    if (!prevRow || prevRow.workStatus === nextStatus) return;
    setSavingWorkStatusClientId(clientId);
    setBaseClients((prev) =>
      prev.map((row) => (row.id === clientId ? { ...row, workStatus: nextStatus } : row)),
    );
    if (!isAgentManager && nextStatus === 'Неактивен') {
      setArchiveExpanded(true);
    }
    try {
      const resp = await updateAdminClientWorkStatus(clientId, nextStatus);
      setBaseClients((prev) =>
        prev.map((row) => (row.id === clientId ? { ...row, workStatus: resp.workStatus } : row)),
      );
    } catch (err: unknown) {
      setBaseClients((prev) =>
        prev.map((row) => (row.id === clientId ? { ...row, workStatus: prevRow.workStatus } : row)),
      );
      window.dispatchEvent(
        new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось изменить рабочий статус клиента') }),
      );
    } finally {
      setSavingWorkStatusClientId(null);
    }
  }

  function openTariffModal(client: Pick<ClientRow, 'id' | 'name'>, mode: 'list' | 'create') {
    setTariffModalState({
      clientId: client.id,
      clientName: client.name,
      mode,
    });
  }

  function resetClientPages() {
    setPage(1);
    setActivePage(1);
    setArchivePage(1);
  }

  function updatePageSize(nextPageSize: number) {
    setPageSize(nextPageSize);
    resetClientPages();
  }

  function openClientEditor(row: ClientRow) {
    setCardClientId(row.id);
    setCardClientData({
      name: row.name,
      inn: row.inn,
      phone: row.phone,
      contact: row.contact,
      telegramNotificationsChatId: row.telegramNotificationsChatId,
      telegramAutoPauseEnabled: row.telegramAutoPauseEnabled,
      uniqueProjectNamesEnabled: row.uniqueProjectNamesEnabled,
      internalClientId: row.internalClientId,
      tableUrl: row.tableUrl,
      pixelTableUrl: row.pixelTableUrl,
      login: row.login,
    });
  }

  function closeSummaryModal() {
    setSelectedClientId(null);
    setCollectionState(null);
    setCollectionLoading(false);
    setCollectionLastInfo(null);
    setPauseSnapshotExpanded(false);
  }

  function getPageInfo(rows: ClientRow[], currentPage: number) {
    const totalRows = rows.length;
    const totalPagesCount = Math.max(1, Math.ceil(totalRows / pageSize));
    const safePage = Math.min(Math.max(currentPage, 1), totalPagesCount);
    const pageStart = (safePage - 1) * pageSize;
    const pageEnd = pageStart + pageSize;
    return {
      totalRows,
      totalPagesCount,
      safePage,
      pageRows: rows.slice(pageStart, pageEnd),
      totals: calculateTotals(rows),
    };
  }

  function renderPager(
    pageInfo: ReturnType<typeof getPageInfo>,
    setCurrentPage: (nextPage: number) => void,
    top = false,
  ) {
    return (
      <div className={`table-footer${top ? ' table-footer--top' : ''}`}>
        Показано {pageInfo.pageRows.length} из {pageInfo.totalRows}
        <div className="spacer" />
        <div className="pager">
          <button
            className="pager__btn"
            disabled={pageInfo.safePage <= 1}
            onClick={() => setCurrentPage(Math.max(1, pageInfo.safePage - 1))}
          >
            ‹
          </button>
          <span className="pager__info">
            {pageInfo.safePage} / {pageInfo.totalPagesCount}
          </span>
          <button
            className="pager__btn"
            disabled={pageInfo.safePage >= pageInfo.totalPagesCount}
            onClick={() => setCurrentPage(Math.min(pageInfo.totalPagesCount, pageInfo.safePage + 1))}
          >
            ›
          </button>
          <select
            className="pager__size"
            value={pageSize}
            onChange={(e) => updatePageSize(Number(e.target.value))}
          >
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>
    );
  }

  function renderClientsTable({
    title,
    subtitle,
    rows,
    currentPage,
    setCurrentPage,
    emptyText,
    collapsed = false,
    headerAction,
  }: {
    title?: string;
    subtitle?: string;
    rows: ClientRow[];
    currentPage: number;
    setCurrentPage: (nextPage: number) => void;
    emptyText: string;
    collapsed?: boolean;
    headerAction?: ReactNode;
  }) {
    const pageInfo = getPageInfo(rows, currentPage);
    return (
      <div className={`table-card client-list-section${collapsed ? ' client-list-section--collapsed' : ''}`}>
        {title && (
          <div className="client-list-section__header">
            <div>
              <div className="client-list-section__title">{title}</div>
              {subtitle && <div className="sub">{subtitle}</div>}
            </div>
            {headerAction}
          </div>
        )}
        {collapsed ? (
          <div className="client-list-section__collapsed-body sub">
            {rows.length > 0 ? `В архиве клиентов: ${rows.length}` : emptyText}
          </div>
        ) : (
          <>
        {renderPager(pageInfo, setCurrentPage, true)}
        <div className="table-scroll">
          <table className="table admin-clients-table">
            <thead>
              <tr>
                <th className="table-sticky-cell table-sticky-cell--lead">ID</th>
                <th>Клиент</th>
                <th>Проекты</th>
                <th>Сбор данных</th>
                <th>Тариф</th>
                <th>Остаток</th>
                <th>Работа</th>
                <th>Данные</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {error && (
                <tr>
                  <td colSpan={9} style={{ color: '#d00', padding: 16 }}>
                    {error}
                  </td>
                </tr>
              )}
              {!error && loading && (
                <tr>
                  <td colSpan={9} className="muted" style={{ padding: 16 }}>
                    Загрузка списка клиентов…
                  </td>
                </tr>
              )}
              {!error && !loading && pageInfo.pageRows.length === 0 && (
                <tr>
                  <td colSpan={9} className="muted" style={{ padding: 16 }}>
                    {emptyText}
                  </td>
                </tr>
              )}
              {!error &&
                !loading &&
                pageInfo.pageRows.map((row) => {
                  const blacklistTotal = (row.pendingBlacklistAdds ?? 0) + (row.pendingBlacklistDeletes ?? 0);
                  const hasEvents = (row.pendingChanges ?? 0) > 0 || (row.pendingCreates ?? 0) > 0 || blacklistTotal > 0;
                  const isDebt = row.workStatus !== 'Неактивен' && row.financeStatus === 'Долг';
                  return (
                    <tr
                      key={row.id}
                      className={`client-row${isDebt ? ' row--debt' : ''}`}
                    >
                      <td className="muted table-sticky-cell table-sticky-cell--lead">{row.id}</td>
                      <td>
                        <div className="name">{row.name}</div>
                        {hasEvents && (
                          <div className="chip-stack">
                            {row.pendingChanges > 0 && (
                              <span className="badge badge--orange">Изменения: {row.pendingChanges}</span>
                            )}
                            {row.pendingCreates > 0 && (
                              <span className="badge badge--gray">Создания: {row.pendingCreates}</span>
                            )}
                            {blacklistTotal > 0 && <span className="badge badge--gray">ЧС: {blacklistTotal}</span>}
                          </div>
                        )}
                      </td>
                      <td>{row.projectCount}</td>
                      <td>
                        <span className={getCollectionBadgeClass(row.dataCollectionStatus)}>
                          {row.dataCollectionStatus}
                        </span>
                      </td>
                      <td>{row.tariffAmount == null ? '-' : row.tariffAmount}</td>
                      <td>
                        <div className={isDebt ? 'remaining-negative' : undefined}>{row.remaining}</div>
                        {row.workStatus !== 'Неактивен' && row.financeStatus && (
                          <div className="client-finance-badge-row">
                            <span className={getFinanceBadgeClass(row.financeStatus)}>{row.financeStatus}</span>
                          </div>
                        )}
                      </td>
                      <td>
                        <select
                          className={getWorkStatusSelectClass(row.workStatus)}
                          value={row.workStatus}
                          disabled={savingWorkStatusClientId === row.id}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => {
                            e.stopPropagation();
                            void handleWorkStatusChange(row.id, e.target.value as ClientWorkStatus);
                          }}
                        >
                          {WORK_STATUSES.map((statusOption) => (
                            <option key={statusOption} value={statusOption}>
                              {statusOption}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>{row.totalVolume}</td>
                      <td>
                        <div className="client-row-actions">
                          <button
                            className="btn btn--primary"
                            type="button"
                            onClick={() => setSelectedClientId(row.id)}
                          >
                            Сводка
                          </button>
                          <button
                            className="btn btn--secondary"
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              openClientEditor(row);
                            }}
                          >
                            Карточка
                          </button>
                          <button
                            className="btn btn--secondary"
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              void handleOpenClientCabinet(row.id);
                            }}
                            disabled={openingClientCabinetId === row.id}
                          >
                            {openingClientCabinetId === row.id ? 'Переходим…' : 'ЛК'}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
        {renderPager(pageInfo, setCurrentPage)}
        <div className="client-list-section__totals sub">
          <span>
            Итого: клиентов {rows.length}, проектов {pageInfo.totals.totalProjects}
          </span>
          <span>
            Суммарный остаток: {pageInfo.totals.totalRemaining}, общий объём данных за период: {pageInfo.totals.totalVolume}
          </span>
        </div>
          </>
        )}
      </div>
    );
  }

  function renderClientSummaryMetrics() {
    if (!selectedClient) return null;
    return (
      <>
        <div className="summary-grid summary-grid--priority">
          <div className="summary-card">
            <div className="sub">Среднее 7 раб. дн.</div>
            <div className="value">{formatSummaryNumber(selectedSummaryAverage7)}</div>
            <div className="sub">Вт-Сб, от сегодня</div>
          </div>
          <div className="summary-card">
            <div className="sub">Среднее 3 раб. дн.</div>
            <div className="value">{formatSummaryNumber(selectedSummaryAverage3)}</div>
            <div className="sub">Вт-Сб, от сегодня</div>
          </div>
          <div className="summary-card summary-card--sources">
            <div className="sub">Источники за период</div>
            <div className="client-summary-source-breakdown">
              {RAW_SOURCE_CODES.map((source) => (
                <span key={source} className={selectedSummarySources.includes(source) ? 'is-active' : undefined}>
                  {toDisplaySourceCode(source)}: {formatSummaryNumber(Number(selectedClient.usedPeriodBySource[source] ?? 0))}
                </span>
              ))}
            </div>
          </div>
        </div>
        <div className="summary-grid summary-grid--secondary">
          <div className="summary-card">
            <div className="sub">Проектов</div>
            <div className="value">{selectedClient.projectCount}</div>
          </div>
          <div className="summary-card">
            <div className="sub">Данных за период</div>
            <div className="value">{formatSummaryNumber(selectedSummaryUsedPeriod)}</div>
          </div>
          <div className="summary-card">
            <div className="sub">Тариф</div>
            <div className="value">{selectedClient.tariffAmount == null ? '-' : selectedClient.tariffAmount}</div>
          </div>
          <div className="summary-card">
            <div className="sub">Баланс</div>
            <div className={`value${selectedClient.financeStatus === 'Долг' ? ' value--negative' : ''}`}>
              Остаток: {selectedClient.remaining}
            </div>
            <div className="sub">Использовано: {selectedClient.usedTotal}</div>
            <div className="sub">Начислено: {selectedAccrued}</div>
          </div>
        </div>
      </>
    );
  }

  function renderSummarySourceButtons() {
    return (
      <div className="client-summary-sources__buttons" aria-label="Источники">
        {summarySourceOptions.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`dashboard-source${selectedSummarySources.includes(option.value) ? ' dashboard-source--active' : ''}`}
            onClick={() => toggleSummarySource(option.value)}
            title={`Источник ${option.label}`}
          >
            {option.label}
          </button>
        ))}
      </div>
    );
  }

  function renderClientSummaryChart() {
    if (!selectedClient) return null;
    return (
      <section className="client-summary-chart">
        <div className="client-summary-chart__header">
          <div>
            <div className="client-summary__section-title">Динамика данных</div>
            <div className="sub">Данные по дням · последние 30 дней</div>
          </div>
          <strong>{formatSummaryNumber(selectedSummaryDaily30.reduce((sum, point) => sum + point.value, 0))}</strong>
        </div>
        <DashboardDailyChart data={selectedSummaryDaily30} />
      </section>
    );
  }

  function renderClientSummaryContent() {
    if (!selectedClient) return null;
    return (
      <>
        <div className="client-summary__header">
          <div className="client-summary__info">
            <div className="client-summary__status-list">
              <div className="client-summary__status-item">
                <span className="sub">Сбор данных</span>
                <span className={getCollectionBadgeClass(selectedClient.dataCollectionStatus)}>
                  {selectedClient.dataCollectionStatus}
                </span>
              </div>
              <div className="client-summary__status-item">
                <span className="sub">Работа</span>
                <span className={getWorkStatusSelectClass(selectedClient.workStatus).replaceAll('client-work-select', 'client-work-badge')}>
                  {selectedClient.workStatus}
                </span>
              </div>
              <div className="client-summary__status-item">
                <span className="sub">Авто-контроль</span>
                <span className={selectedClient.autoLimitControlEnabled ? 'badge badge--green' : 'badge badge--gray'}>
                  {selectedClient.autoLimitControlEnabled ? 'Авто + ручной' : 'Ручной'}
                </span>
              </div>
            </div>
          </div>
          <div className="client-summary__actions-panel">
            <div className="client-summary__panel-grid">
              <section className="client-summary__section">
                <div className="client-summary__section-title">Навигация</div>
                <div className="client-summary__section-actions">
                  {onOpenClientChanges && (
                    <button
                      type="button"
                      className="btn btn--secondary"
                      onClick={() => {
                        if (!isAgentManager) closeSummaryModal();
                        onOpenClientChanges(selectedClient.id, selectedClient.name);
                      }}
                    >
                      Изменения клиента
                      {(selectedClient.pendingChanges > 0 || selectedClient.pendingCreates > 0) && (
                        <span className="btn__meta">
                          {selectedClient.pendingChanges > 0 && (
                            <span className="badge badge--orange">Изм: {selectedClient.pendingChanges}</span>
                          )}
                          {selectedClient.pendingCreates > 0 && (
                            <span className="badge badge--gray">Созд: {selectedClient.pendingCreates}</span>
                          )}
                        </span>
                      )}
                    </button>
                  )}
                  {onOpenClientBlacklistChanges && (
                    <button
                      type="button"
                      className="btn btn--secondary"
                      onClick={() => {
                        if (!isAgentManager) closeSummaryModal();
                        onOpenClientBlacklistChanges(selectedClient.id, selectedClient.name);
                      }}
                    >
                      События ЧС
                      {selectedBlacklistTotal > 0 && (
                        <span className="btn__meta">
                          <span className="badge badge--orange">ЧС: {selectedBlacklistTotal}</span>
                        </span>
                      )}
                    </button>
                  )}
                  {onOpenClientProjects && (
                    <button
                      type="button"
                      className="btn btn--secondary"
                      onClick={() => {
                        if (!isAgentManager) closeSummaryModal();
                        onOpenClientProjects(selectedClient.id, selectedClient.name);
                      }}
                    >
                      Перейти к проектам
                    </button>
                  )}
                </div>
              </section>

              <section className="client-summary__section">
                <div className="client-summary__section-title">Финансы</div>
                <div className="client-summary__section-actions">
                  {!isAgentManager ? (
                    <>
                      <button
                        type="button"
                        className="btn btn--primary"
                        onClick={() => {
                          const client = selectedClient;
                          closeSummaryModal();
                          openTariffModal(client, 'create');
                        }}
                      >
                        + Новый тариф
                      </button>
                      <button
                        type="button"
                        className="btn btn--secondary"
                        onClick={() => {
                          const client = selectedClient;
                          closeSummaryModal();
                          if (onOpenClientBalance) {
                            onOpenClientBalance(client.id, client.name, 'tariff');
                            return;
                          }
                          openTariffModal(client, 'list');
                        }}
                      >
                        Управление тарифами
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--secondary"
                      onClick={() => {
                        if (onOpenClientBalance) {
                          onOpenClientBalance(selectedClient.id, selectedClient.name, 'tariff');
                          return;
                        }
                        openTariffModal(selectedClient, 'list');
                      }}
                    >
                      Смотреть тарифы
                    </button>
                  )}
                </div>
              </section>

              <section className="client-summary__section">
                <div className="client-summary__section-title">Владелец</div>
                <div className="client-summary__section-actions client-summary__owner-actions">
                  <div className="sub">
                    Текущий владелец: <b>{formatOwnerLabel(selectedClient)}</b>
                  </div>
                  {!isAgentManager && (
                    <>
                      <select
                        className="client-summary__owner-select"
                        value={ownerTarget}
                        onChange={(e) => setOwnerTarget(e.target.value)}
                      >
                        <option value="admin">Админ</option>
                        {agents
                          .filter((agent) => !agent.isDisabled || ownerTarget === `agent:${agent.id}`)
                          .map((agent) => (
                            <option key={agent.id} value={`agent:${agent.id}`}>
                              {agent.name} ({agent.login})
                            </option>
                          ))}
                      </select>
                      <button
                        type="button"
                        className="btn btn--secondary"
                        disabled={
                          (selectedClient.ownerType === 'admin' && ownerTarget === 'admin')
                          || (selectedClient.ownerType === 'agent' && ownerTarget === `agent:${selectedClient.ownerUser?.id ?? 0}`)
                        }
                        onClick={() => {
                          void handleTransferOwner();
                        }}
                      >
                        Переназначить клиента
                      </button>
                    </>
                  )}
                </div>
              </section>
            </div>

            {!isAgentManager && (
              <section className="client-summary__section client-summary__section--control">
                <div className="client-summary__section-title">Управление</div>
                <div className="client-summary__control-actions">
                  <button
                    type="button"
                    className="btn btn--secondary client-summary__button--stacked"
                    onClick={() => {
                      void handleToggleAutoLimitControl();
                    }}
                  >
                    <span>
                      {selectedClient.autoLimitControlEnabled
                        ? 'Выключить авто-контроль лимитов'
                        : 'Включить авто-контроль лимитов'}
                    </span>
                    <span className="sub" style={{ opacity: 0.9 }}>
                      Режим: {selectedClient.autoLimitControlEnabled ? 'автоматический + ручной' : 'полностью ручной'}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="btn btn--secondary client-summary__button--stacked"
                    disabled={
                      collectionLoading
                      || collectionBusy
                      || !collectionState
                      || !collectionState.actionEnabled
                    }
                    onClick={() => {
                      void handleToggleCollection();
                    }}
                    title={collectionState?.actionDisabledReason || undefined}
                  >
                    <span>
                      {collectionBusy
                        ? 'Выполняем…'
                        : (collectionState?.actionLabel || 'Поставить проекты на паузу')}
                    </span>
                    <span className="sub client-summary__button-details">
                      <span className="client-summary__button-detail-row">
                        <span>Сбор данных:</span>
                        {collectionLoading ? (
                          <span className="badge badge--gray">Загрузка…</span>
                        ) : (
                          <span
                            className={
                              collectionState?.dataCollectionStatus === 'На паузе'
                                ? 'badge badge--orange'
                                : 'badge badge--green'
                            }
                          >
                            {collectionState?.dataCollectionStatus ?? '—'}
                          </span>
                        )}
                      </span>
                      <span className="client-summary__button-detail-row">
                        <span>Изменения проектов:</span>
                        {collectionLoading ? (
                          <span className="badge badge--gray">Загрузка…</span>
                        ) : (
                          <span
                            className={
                              collectionState?.projectsMutationLocked
                                ? 'badge badge--orange'
                                : 'badge badge--green'
                            }
                          >
                            {collectionState?.projectsMutationLocked ? 'Заблокированы' : 'Разрешены'}
                          </span>
                        )}
                      </span>
                    </span>
                  </button>
                </div>
                <div className="client-summary__control-notes">
                  {!!collectionState?.actionDisabledReason && (
                    <span className="sub" style={{ color: '#a55' }}>
                      {collectionState.actionDisabledReason}
                    </span>
                  )}
                  {collectionBusy && collectionRunInfo && (
                    <span className="sub">
                      {collectionRunInfo.mode === 'pause' ? 'Обрабатываем паузу' : 'Обрабатываем восстановление'}
                      {collectionRunInfo.total > 0 ? `: 0/${collectionRunInfo.total}` : '...'}
                    </span>
                  )}
                  {operationActive && projectOperation && (
                    <span className="sub" style={{ color: '#6b4ce6' }}>
                      {getProjectOperationUserMessage(projectOperation, { isAdmin: true })} Выполнено: {projectOperation.completedCount}/{projectOperation.totalCount}. Следующая попытка: {projectOperation.nextAttemptAt ? new Date(projectOperation.nextAttemptAt).toLocaleTimeString('ru-RU') : 'скоро'}.
                    </span>
                  )}
                  {!collectionBusy && !!collectionLastInfo && !/Выполнено:\s*0\/0\.\s*Пропущено:\s*0\.\s*Ошибок:\s*0\./.test(collectionLastInfo) && (
                    <span className="sub">{collectionLastInfo}</span>
                  )}
                </div>
              </section>
            )}
          </div>
        </div>
        {renderClientSummaryChart()}
        {renderClientSummaryMetrics()}
        {!isAgentManager && (
          <div className="client-summary__snapshot">
            <button
              type="button"
              className="btn btn--ghost client-summary__snapshot-toggle"
              onClick={() => setPauseSnapshotExpanded((prev) => !prev)}
              title={pauseSnapshotExpanded ? 'Свернуть список' : 'Развернуть список'}
            >
              <span className="client-summary__snapshot-title">
                <span className="client-summary__snapshot-icon">i</span>
                <span>
                  Проекты из последней массовой паузы
                  {collectionState ? ` (${collectionState.snapshotProjects.length})` : ''}
                </span>
              </span>
              <span className="sub" style={{ fontSize: 12 }}>
                {pauseSnapshotExpanded ? '▾' : '▸'}
              </span>
            </button>

            {pauseSnapshotExpanded && (
              <div className="client-summary__snapshot-list">
                {collectionLoading && <div className="sub">Загрузка списка…</div>}
                {!collectionLoading && (!collectionState || collectionState.snapshotProjects.length === 0) && (
                  <div className="sub">Снимок отсутствует.</div>
                )}
                {!collectionLoading && collectionState && collectionState.snapshotProjects.length > 0 && (
                  <div className="client-summary__snapshot-items">
                    {collectionState.snapshotProjects.map((project) => (
                      <div key={project.id} className="client-summary__snapshot-item">
                        <span>{formatProjectNameForDisplay(project.name)} (id: {project.id})</span>
                        <span
                          className={
                            project.status === 'Активен'
                              ? 'badge badge--green'
                              : project.status === 'На паузе'
                                ? 'badge badge--orange'
                                : project.status === 'Архив'
                                  ? 'badge badge--info'
                                : 'badge badge--gray'
                          }
                        >
                          {project.status}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </>
    );
  }

  if (!isAgentManager) {
    return (
      <>
        <div className="admin-clients-screen">
          <div className="table-card admin-clients-toolbar-card">
            <div className="table-toolbar toolbar-split">
              <div className="filters toolbar-left">
                <DateRangeFilter
                  from={range.from}
                  to={range.to}
                  onChange={(next) => {
                    setRange(next);
                    resetClientPages();
                  }}
                />
                <input
                  type="search"
                  placeholder="Поиск по имени / ID клиента"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    resetClientPages();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      resetClientPages();
                    }
                  }}
                  style={{ minWidth: 220, flex: 1 }}
                />
              </div>
              <div className="actions toolbar-right">
                {loading ? (
                  <span className="toolbar-meta">Загрузка…</span>
                ) : (
                  <span className="toolbar-meta">
                    Всего клиентов: {clients.length} · Период: <DateRangeCompact from={range.from} to={range.to} />
                  </span>
                )}
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => setCreateOpen(true)}
                >
                  + Новый клиент
                </button>
              </div>
            </div>
          </div>

          {renderClientsTable({
            title: 'Активные клиенты',
            rows: activeClients,
            currentPage: activePage,
            setCurrentPage: setActivePage,
            emptyText: 'Нет активных клиентов',
          })}

          {renderClientsTable({
            title: 'Архив',
            subtitle: `Клиенты со статусом «Работа: Неактивен» · ${archivedClients.length}`,
            rows: archivedClients,
            currentPage: archivePage,
            setCurrentPage: setArchivePage,
            emptyText: 'В архиве пока нет клиентов',
            collapsed: !archiveExpanded,
            headerAction: (
              <button
                type="button"
                className="btn btn--secondary client-list-section__toggle"
                onClick={() => setArchiveExpanded((prev) => !prev)}
                aria-expanded={archiveExpanded}
              >
                {archiveExpanded ? 'Свернуть' : 'Развернуть'}
                <span aria-hidden="true">{archiveExpanded ? '▾' : '▸'}</span>
              </button>
            ),
          })}
        </div>

        {selectedClient && (
          <div
            className="modal-backdrop client-summary-modal__backdrop"
            onMouseDown={(e) => {
              if (e.target === e.currentTarget) closeSummaryModal();
            }}
          >
            <div
              role="dialog"
              aria-modal="true"
              className="modal client-summary-modal"
              onMouseDown={(e) => e.stopPropagation()}
            >
              <div className="client-summary-modal__header">
                <div>
                  <div className="client-summary-modal__eyebrow">Сводка клиента</div>
                  <div className="client-summary-modal__title">{selectedClient.name}</div>
                  <div className="client-summary-modal__meta">
                    ID: {selectedClient.id} · Необработанных событий: {selectedPendingTotal}
                  </div>
                </div>
                <div className="client-summary-modal__header-actions">
                  {renderSummarySourceButtons()}
                  <button
                    type="button"
                    className="icon-btn client-summary-modal__close"
                    aria-label="Закрыть"
                    onClick={closeSummaryModal}
                  >
                    ✕
                  </button>
                </div>
              </div>
              <div className="client-summary-modal__body">
                <div className="client-summary-card client-summary-card--modal">
                  {renderClientSummaryContent()}
                </div>
              </div>
            </div>
          </div>
        )}

        {createOpen && (
          <AdminCreateClientModal
            managerRole={managerRole}
            onClose={() => setCreateOpen(false)}
            onCreated={handleClientCreated}
          />
        )}
        {cardClientId && cardClientData && (
          <AdminClientCardModal
            clientId={cardClientId}
            managerRole={managerRole}
            initialName={cardClientData.name}
            initialInn={cardClientData.inn || undefined}
            initialPhone={cardClientData.phone || undefined}
            initialContact={cardClientData.contact || undefined}
            initialTelegramNotificationsChatId={cardClientData.telegramNotificationsChatId || undefined}
            initialTelegramAutoPauseEnabled={cardClientData.telegramAutoPauseEnabled}
            initialUniqueProjectNamesEnabled={cardClientData.uniqueProjectNamesEnabled}
            initialInternalClientId={cardClientData.internalClientId || undefined}
            initialTableUrl={cardClientData.tableUrl || undefined}
            initialPixelTableUrl={cardClientData.pixelTableUrl || undefined}
            initialLogin={cardClientData.login}
            onClose={() => setCardClientId(null)}
            onUpdated={() => {
              setRefreshKey((x) => x + 1);
              setCardClientId(null);
            }}
          />
        )}
        {tariffModalState && (
          <TariffManagerModal
            targetId={tariffModalState.clientId}
            targetName={tariffModalState.clientName}
            title={`Тарифы клиента: ${tariffModalState.clientName}`}
            onClose={() => setTariffModalState(null)}
            onChanged={() => {
              setRefreshKey((x) => x + 1);
            }}
            readOnly={false}
            initialEditorMode={tariffModalState.mode === 'create' ? 'create' : null}
            createOnly={tariffModalState.mode === 'create'}
            fetchTariffs={fetchAdminClientTariffs}
            createTariff={createAdminClientTariff}
            updateTariff={updateAdminTariff}
            createTariffOp={createAdminTariffOp}
            fetchTariffOps={fetchAdminTariffOps}
          />
        )}
      </>
    );
  }

  return (
    <>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div className="table-card">
        <div className="table-toolbar toolbar-split">
          <div className="filters toolbar-left">
            <DateRangeFilter
              from={range.from}
              to={range.to}
              onChange={(next) => {
                setRange(next);
                setPage(1);
              }}
            />
            <input
              type="search"
              placeholder="Поиск по имени / ID клиента"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  setPage(1);
                }
              }}
              style={{ minWidth: 220, flex: 1 }}
            />
          </div>
          <div className="actions toolbar-right">
            {loading ? (
              <span className="toolbar-meta">Загрузка…</span>
            ) : (
              <span className="toolbar-meta">
                Всего клиентов: {clients.length} · Период: <DateRangeCompact from={range.from} to={range.to} />
              </span>
            )}
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => setCreateOpen(true)}
            >
              + Новый клиент
            </button>
          </div>
        </div>

        <div className="table-footer table-footer--top">
          Показано {pageRows.length} из {total}
          <div className="spacer" />
          <div className="pager">
            <button
              className="pager__btn"
              disabled={pageSafe <= 1}
              onClick={() => {
                const p = Math.max(1, pageSafe - 1);
                setPage(p);
              }}
            >
              ‹
            </button>
            <span className="pager__info">
              {pageSafe} / {totalPages}
            </span>
            <button
              className="pager__btn"
              disabled={pageSafe >= totalPages}
              onClick={() => {
                const p = Math.min(totalPages, pageSafe + 1);
                setPage(p);
              }}
            >
              ›
            </button>
            <select
              className="pager__size"
              value={pageSize}
              onChange={(e) => {
                const s = Number(e.target.value);
                setPageSize(s);
                setPage(1);
              }}
            >
              <option value={10}>10</option>
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </div>
        </div>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th className="table-sticky-cell table-sticky-cell--lead">ID</th>
                <th>Название клиента</th>
                <th>Проектов</th>
                <th>Сбор данных</th>
                <th>Тариф</th>
                <th>Остаток</th>
                <th>Работа</th>
                <th>Данных за период</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {error && (
                <tr>
                  <td colSpan={9} style={{ color: '#d00', padding: 16 }}>
                    {error}
                  </td>
                </tr>
              )}
              {!error && loading && (
                <tr>
                  <td colSpan={9} className="muted" style={{ padding: 16 }}>
                    Загрузка списка клиентов…
                  </td>
                </tr>
              )}
              {!error && !loading && pageRows.length === 0 && (
                <tr>
                  <td colSpan={9} className="muted" style={{ padding: 16 }}>
                    Клиенты не найдены.
                  </td>
                </tr>
              )}
              {!error &&
                !loading &&
                pageRows.map((row) => {
                  const blacklistTotal = (row.pendingBlacklistAdds ?? 0) + (row.pendingBlacklistDeletes ?? 0);
                  const hasEvents = (row.pendingChanges ?? 0) > 0 || (row.pendingCreates ?? 0) > 0 || blacklistTotal > 0;
                  const isDebt = row.workStatus !== 'Неактивен' && row.financeStatus === 'Долг';
                  return (
                    <tr
                      key={row.id}
                      className={`client-row${isDebt ? ' row--debt' : ''}`}
                      style={{ cursor: 'pointer' }}
                      onClick={() => {
                        setSelectedClientId((prev) => (prev === row.id ? null : row.id));
                      }}
                    >
                    <td className="muted table-sticky-cell table-sticky-cell--lead">{row.id}</td>
                    <td>
                      <div className="name">{row.name}</div>
                      {hasEvents && (
                        <div className="chip-stack">
                          {row.pendingChanges > 0 && (
                            <span className="badge badge--orange">Изменения: {row.pendingChanges}</span>
                          )}
                          {row.pendingCreates > 0 && (
                            <span className="badge badge--gray">Создания: {row.pendingCreates}</span>
                          )}
                          {blacklistTotal > 0 && (
                            <span className="badge badge--gray">ЧС: {blacklistTotal}</span>
                          )}
                        </div>
                      )}
                    </td>
                    <td>{row.projectCount}</td>
                    <td>
                      <span className={getCollectionBadgeClass(row.dataCollectionStatus)}>
                        {row.dataCollectionStatus}
                      </span>
                    </td>
                    <td>{row.tariffAmount == null ? '-' : row.tariffAmount}</td>
                    <td>
                      <div className={isDebt ? 'remaining-negative' : undefined}>{row.remaining}</div>
                      {row.workStatus !== 'Неактивен' && row.financeStatus && (
                        <div className="client-finance-badge-row">
                          <span className={getFinanceBadgeClass(row.financeStatus)}>{row.financeStatus}</span>
                        </div>
                      )}
                    </td>
                    <td>
                      <select
                        className={getWorkStatusSelectClass(row.workStatus)}
                        value={row.workStatus}
                        disabled={savingWorkStatusClientId === row.id}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => {
                          e.stopPropagation();
                          void handleWorkStatusChange(row.id, e.target.value as ClientWorkStatus);
                        }}
                      >
                        {WORK_STATUSES.map((statusOption) => (
                          <option key={statusOption} value={statusOption}>
                            {statusOption}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>{row.totalVolume}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {!isAgentManager && (
                          <button
                            className="btn btn--primary"
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              openTariffModal(row, 'create');
                            }}
                          >
                            Тариф
                          </button>
                        )}
                        <button
                          className="btn btn--primary"
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setCardClientId(row.id);
                            setCardClientData({
                              name: row.name,
                              inn: row.inn,
                              phone: row.phone,
                              contact: row.contact,
                              telegramNotificationsChatId: row.telegramNotificationsChatId,
                              telegramAutoPauseEnabled: row.telegramAutoPauseEnabled,
                              uniqueProjectNamesEnabled: row.uniqueProjectNamesEnabled,
                              internalClientId: row.internalClientId,
                              tableUrl: row.tableUrl,
                              pixelTableUrl: row.pixelTableUrl,
                              login: row.login,
                            });
                          }}
                        >
                          Карточка
                        </button>
                        <button
                          className="btn btn--secondary"
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleOpenClientCabinet(row.id);
                          }}
                          disabled={openingClientCabinetId === row.id}
                        >
                          {openingClientCabinetId === row.id ? 'Переходим…' : 'ЛК'}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

      <div className="table-footer">
          Показано {pageRows.length} из {total}
          <div className="spacer" />
          <div className="pager">
            <button
              className="pager__btn"
              disabled={pageSafe <= 1}
              onClick={() => {
                const p = Math.max(1, pageSafe - 1);
                setPage(p);
              }}
            >
              ‹
            </button>
            <span className="pager__info">
              {pageSafe} / {totalPages}
            </span>
            <button
              className="pager__btn"
              disabled={pageSafe >= totalPages}
              onClick={() => {
                const p = Math.min(totalPages, pageSafe + 1);
                setPage(p);
              }}
            >
              ›
            </button>
            <select
              className="pager__size"
              value={pageSize}
              onChange={(e) => {
                const s = Number(e.target.value);
                setPageSize(s);
                setPage(1);
              }}
            >
              <option value={10}>10</option>
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </div>
        </div>
        <div
          style={{
            padding: '8px 16px 12px',
            borderTop: '1px dashed #eee',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 12,
            fontSize: 13,
          }}
          className="sub"
        >
          <span>
            Итого: клиентов {filtered.length}, проектов {totals.totalProjects}
          </span>
          <span>
            Суммарный остаток: {totals.totalRemaining}, общий объём данных за период: {totals.totalVolume}
          </span>          
        </div>
      </div>

      {selectedClient ? (
        <div className="table-card client-summary-card">
          <div className="client-summary__header">
            <div className="client-summary__info">
              <div className="client-summary__title">{selectedClient.name}</div>
              <div className="client-summary__meta">
                <span className="sub">ID: {selectedClient.id}</span>
                {selectedClient.financeStatus === 'Долг' && <span className="badge badge--orange">Долг</span>}
                <span className="badge badge--gray">Необработанных событий: {selectedPendingTotal}</span>
              </div>
              <div className="client-summary__status-list">
                <div className="client-summary__status-item">
                  <span className="sub">Сбор данных</span>
                  <span className={getCollectionBadgeClass(selectedClient.dataCollectionStatus)}>
                    {selectedClient.dataCollectionStatus}
                  </span>
                </div>
                <div className="client-summary__status-item">
                  <span className="sub">Авто-контроль</span>
                  <span className={selectedClient.autoLimitControlEnabled ? 'badge badge--green' : 'badge badge--gray'}>
                    {selectedClient.autoLimitControlEnabled ? 'Авто + ручной' : 'Ручной'}
                  </span>
                </div>
              </div>
            </div>
            <div className="client-summary__actions-panel">
              <div className="client-summary__panel-grid">
                <section className="client-summary__section">
                  <div className="client-summary__section-title">Навигация</div>
                  <div className="client-summary__section-actions">
                    {onOpenClientChanges && (
                      <button
                        type="button"
                        className="btn btn--secondary"
                        onClick={() => onOpenClientChanges(selectedClient.id, selectedClient.name)}
                      >
                        Изменения клиента
                        {(selectedClient.pendingChanges > 0 || selectedClient.pendingCreates > 0) && (
                          <span className="btn__meta">
                            {selectedClient.pendingChanges > 0 && (
                              <span className="badge badge--orange">Изм: {selectedClient.pendingChanges}</span>
                            )}
                            {selectedClient.pendingCreates > 0 && (
                              <span className="badge badge--gray">Созд: {selectedClient.pendingCreates}</span>
                            )}
                          </span>
                        )}
                      </button>
                    )}
                    {onOpenClientBlacklistChanges && (
                      <button
                        type="button"
                        className="btn btn--secondary"
                        onClick={() => onOpenClientBlacklistChanges(selectedClient.id, selectedClient.name)}
                      >
                        События ЧС
                        {selectedBlacklistTotal > 0 && (
                          <span className="btn__meta">
                            <span className="badge badge--orange">ЧС: {selectedBlacklistTotal}</span>
                          </span>
                        )}
                      </button>
                    )}
                    {onOpenClientProjects && (
                      <button
                        type="button"
                        className="btn btn--secondary"
                        onClick={() => onOpenClientProjects(selectedClient.id, selectedClient.name)}
                      >
                        Перейти к проектам
                      </button>
                    )}
                  </div>
                </section>

                <section className="client-summary__section">
                  <div className="client-summary__section-title">Финансы</div>
                  <div className="client-summary__section-actions">
                    {!isAgentManager ? (
                      <button
                        type="button"
                        className="btn btn--primary"
                        onClick={() => {
                          if (onOpenClientBalance) {
                            onOpenClientBalance(selectedClient.id, selectedClient.name, 'tariff');
                            return;
                          }
                          openTariffModal(selectedClient, 'list');
                        }}
                      >
                        Управление тарифами
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--secondary"
                        onClick={() => {
                          if (onOpenClientBalance) {
                            onOpenClientBalance(selectedClient.id, selectedClient.name, 'tariff');
                            return;
                          }
                          openTariffModal(selectedClient, 'list');
                        }}
                      >
                        Смотреть тарифы
                      </button>
                    )}
                  </div>
                </section>

                <section className="client-summary__section">
                  <div className="client-summary__section-title">Владелец</div>
                  <div className="client-summary__section-actions client-summary__owner-actions">
                    <div className="sub">
                      Текущий владелец: <b>{formatOwnerLabel(selectedClient)}</b>
                    </div>
                    {!isAgentManager && (
                      <>
                        <select
                          className="client-summary__owner-select"
                          value={ownerTarget}
                          onChange={(e) => setOwnerTarget(e.target.value)}
                        >
                          <option value="admin">Админ</option>
                          {agents
                            .filter((agent) => !agent.isDisabled || ownerTarget === `agent:${agent.id}`)
                            .map((agent) => (
                              <option key={agent.id} value={`agent:${agent.id}`}>
                                {agent.name} ({agent.login})
                              </option>
                            ))}
                        </select>
                        <button
                          type="button"
                          className="btn btn--secondary"
                          disabled={
                            (selectedClient.ownerType === 'admin' && ownerTarget === 'admin')
                            || (selectedClient.ownerType === 'agent' && ownerTarget === `agent:${selectedClient.ownerUser?.id ?? 0}`)
                          }
                          onClick={() => {
                            void handleTransferOwner();
                          }}
                        >
                          Переназначить клиента
                        </button>
                      </>
                    )}
                  </div>
                </section>
              </div>

              {!isAgentManager && (
                <section className="client-summary__section client-summary__section--control">
                  <div className="client-summary__section-title">Управление</div>
                  <div className="client-summary__control-actions">
                    <button
                      type="button"
                      className="btn btn--secondary client-summary__button--stacked"
                      onClick={() => {
                        void handleToggleAutoLimitControl();
                      }}
                    >
                      <span>
                        {selectedClient.autoLimitControlEnabled
                          ? 'Выключить авто-контроль лимитов'
                          : 'Включить авто-контроль лимитов'}
                      </span>
                      <span className="sub" style={{ opacity: 0.9 }}>
                        Режим: {selectedClient.autoLimitControlEnabled ? 'автоматический + ручной' : 'полностью ручной'}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="btn btn--secondary client-summary__button--stacked"
                      disabled={
                        collectionLoading
                        || collectionBusy
                        || !collectionState
                        || !collectionState.actionEnabled
                      }
                      onClick={() => {
                        void handleToggleCollection();
                      }}
                      title={collectionState?.actionDisabledReason || undefined}
                    >
                      <span>
                        {collectionBusy
                          ? 'Выполняем…'
                          : (collectionState?.actionLabel || 'Поставить проекты на паузу')}
                      </span>
                      <span className="sub client-summary__button-details">
                        <span className="client-summary__button-detail-row">
                          <span>Сбор данных:</span>
                          {collectionLoading ? (
                            <span className="badge badge--gray">Загрузка…</span>
                          ) : (
                            <span
                              className={
                                collectionState?.dataCollectionStatus === 'На паузе'
                                  ? 'badge badge--orange'
                                  : 'badge badge--green'
                              }
                            >
                              {collectionState?.dataCollectionStatus ?? '—'}
                            </span>
                          )}
                        </span>
                        <span className="client-summary__button-detail-row">
                          <span>Изменения проектов:</span>
                          {collectionLoading ? (
                            <span className="badge badge--gray">Загрузка…</span>
                          ) : (
                            <span
                              className={
                                collectionState?.projectsMutationLocked
                                  ? 'badge badge--orange'
                                  : 'badge badge--green'
                              }
                            >
                              {collectionState?.projectsMutationLocked ? 'Заблокированы' : 'Разрешены'}
                            </span>
                          )}
                        </span>
                      </span>
                    </button>
                  </div>
                  <div className="client-summary__control-notes">
                    {!!collectionState?.actionDisabledReason && (
                      <span className="sub" style={{ color: '#a55' }}>
                        {collectionState.actionDisabledReason}
                      </span>
                    )}
                    {collectionBusy && collectionRunInfo && (
                      <span className="sub">
                        {collectionRunInfo.mode === 'pause' ? 'Обрабатываем паузу' : 'Обрабатываем восстановление'}
                        {collectionRunInfo.total > 0 ? `: 0/${collectionRunInfo.total}` : '...'}
                      </span>
                    )}
                    {operationActive && projectOperation && (
                      <span className="sub" style={{ color: '#6b4ce6' }}>
                        {getProjectOperationUserMessage(projectOperation, { isAdmin: true })} Выполнено: {projectOperation.completedCount}/{projectOperation.totalCount}. Следующая попытка: {projectOperation.nextAttemptAt ? new Date(projectOperation.nextAttemptAt).toLocaleTimeString('ru-RU') : 'скоро'}.
                      </span>
                    )}
                    {!collectionBusy && !!collectionLastInfo && !/Выполнено:\s*0\/0\.\s*Пропущено:\s*0\.\s*Ошибок:\s*0\./.test(collectionLastInfo) && (
                      <span className="sub">{collectionLastInfo}</span>
                    )}
                  </div>
                </section>
              )}
            </div>
          </div>
          {renderClientSummaryChart()}
          {renderClientSummaryMetrics()}
          {!isAgentManager && (
          <div style={{ marginTop: 12, borderTop: '1px dashed #eee', paddingTop: 10 }}>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => setPauseSnapshotExpanded((prev) => !prev)}
              style={{
                width: '100%',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                border: '1px solid #e7e9f5',
                background: '#f7f8fc',
                borderRadius: 10,
                padding: '10px 12px',
              }}
              title={pauseSnapshotExpanded ? 'Свернуть список' : 'Развернуть список'}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <span
                  className="sub"
                  style={{
                    width: 16,
                    height: 16,
                    borderRadius: '50%',
                    border: '1px solid #d9dcef',
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    lineHeight: 1,
                  }}
                >
                  i
                </span>
                <span>
                  Проекты из последней массовой паузы
                  {collectionState ? ` (${collectionState.snapshotProjects.length})` : ''}
                </span>
              </span>
              <span className="sub" style={{ fontSize: 12 }}>
                {pauseSnapshotExpanded ? '▾' : '▸'}
              </span>
            </button>

            {pauseSnapshotExpanded && (
              <div style={{ marginTop: 10, display: 'grid', gap: 6 }}>
                {collectionLoading && <div className="sub">Загрузка списка…</div>}
                {!collectionLoading && (!collectionState || collectionState.snapshotProjects.length === 0) && (
                  <div className="sub">Снимок отсутствует.</div>
                )}
                {!collectionLoading && collectionState && collectionState.snapshotProjects.length > 0 && (
                  <div style={{ display: 'grid', gap: 6 }}>
                    {collectionState.snapshotProjects.map((project) => (
                      <div
                        key={project.id}
                        style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}
                      >
                        <span>{formatProjectNameForDisplay(project.name)} (id: {project.id})</span>
                        <span
                          className={
                            project.status === 'Активен'
                              ? 'badge badge--green'
                              : project.status === 'На паузе'
                                ? 'badge badge--orange'
                                : project.status === 'Архив'
                                  ? 'badge badge--info'
                                : 'badge badge--gray'
                          }
                        >
                          {project.status}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          )}
        </div>
      ) : (
        <div className="table-card">
          <div
            style={{
              padding: 24,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 12,
            }}
          >
            <div style={{ fontSize: '1.1rem', fontWeight: 600 }}>Клиент не выбран</div>
            <div className="sub" style={{ textAlign: 'center', maxWidth: 480 }}>
              Выберите клиента в таблице выше, чтобы посмотреть сводку и перейти к его проектам.
            </div>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => {
                window.scrollTo({ top: 0, behavior: 'smooth' });
              }}
            >
              Выбрать клиента
            </button>
          </div>
        </div>
      )}

    </div>
    {createOpen && (
      <AdminCreateClientModal
        managerRole={managerRole}
        onClose={() => setCreateOpen(false)}
        onCreated={handleClientCreated}
      />
    )}
    {cardClientId && cardClientData && (
      <AdminClientCardModal
        clientId={cardClientId}
        managerRole={managerRole}
        initialName={cardClientData.name}
        initialInn={cardClientData.inn || undefined}
        initialPhone={cardClientData.phone || undefined}
        initialContact={cardClientData.contact || undefined}
        initialTelegramNotificationsChatId={cardClientData.telegramNotificationsChatId || undefined}
        initialTelegramAutoPauseEnabled={cardClientData.telegramAutoPauseEnabled}
        initialUniqueProjectNamesEnabled={cardClientData.uniqueProjectNamesEnabled}
        initialInternalClientId={cardClientData.internalClientId || undefined}
        initialTableUrl={cardClientData.tableUrl || undefined}
        initialPixelTableUrl={cardClientData.pixelTableUrl || undefined}
        initialLogin={cardClientData.login}
        onClose={() => setCardClientId(null)}
        onUpdated={() => {
          setRefreshKey((x) => x + 1);
          setCardClientId(null);
        }}
      />
    )}
    {tariffModalState && (
      <TariffManagerModal
        targetId={tariffModalState.clientId}
        targetName={tariffModalState.clientName}
        title={`Тарифы клиента: ${tariffModalState.clientName}`}
        onClose={() => setTariffModalState(null)}
        onChanged={() => {
          setRefreshKey((x) => x + 1);
        }}
        readOnly={isAgentManager}
        initialEditorMode={tariffModalState.mode === 'create' ? 'create' : null}
        createOnly={tariffModalState.mode === 'create'}
        fetchTariffs={fetchAdminClientTariffs}
        createTariff={createAdminClientTariff}
        updateTariff={updateAdminTariff}
        createTariffOp={createAdminTariffOp}
        fetchTariffOps={fetchAdminTariffOps}
      />
    )}
    </>
  );
}

export default AdminClientsScreen;
