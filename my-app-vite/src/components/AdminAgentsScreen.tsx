import { Fragment, useEffect, useMemo, useState } from 'react';
import DateTimeCompact from './DateTimeCompact';
import DateRangeFilter from './DateRangeFilter';
import AdminClientCardModal from './AdminClientCardModal';
import AdminCreateAgentModal from './AdminCreateAgentModal';
import AdminAgentCardModal from './AdminAgentCardModal';
import TariffManagerModal from './TariffManagerModal';
import {
  fetchAdminAgents,
  fetchAdminClientsSummary,
  fetchAdminClientCollectionState,
  fetchAdminClientTariffs,
  fetchAdminTariffOps,
  createAdminClientTariff,
  createAdminTariffOp,
  updateAdminTariff,
  impersonateAgent,
  impersonateClient,
  pauseAdminClientProjects,
  resumeAdminClientProjects,
  transferAdminClientOwner,
  updateAdminClient,
  updateAdminClientWorkStatus,
  type AdminAgentSummaryItem,
  type AdminClientCollectionState,
  type AdminClientSummaryItem,
  type ClientWorkStatus,
  type ClientDataCollectionStatus,
  type ClientFinanceStatus,
} from '../api';
import { formatProjectNameForDisplay } from '../utils/sourceCodeDisplay';

type AdminAgentsScreenProps = {
  onOpenClientProjects?: (clientId: number, clientName: string) => void;
  onOpenClientChanges?: (clientId: number, clientName: string) => void;
  onOpenClientBlacklistChanges?: (clientId: number, clientName: string) => void;
  onOpenClientBalance?: (clientId: number, clientName: string, action: 'tariff') => void;
};

type AgentClientRow = {
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
  remaining: number;
  totalVolume: number;
  totalLimit: number;
  usedTotal: number;
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
  inn?: string | null;
  phone?: string | null;
  contact?: string | null;
};

type DateRange = { from: string; to: string };

const WORK_STATUSES: ClientWorkStatus[] = [
  'В работе',
  'Ждём оплату',
  'Ждём данные',
  'На согласовании',
  'Пауза по клиенту',
  'Неактивен',
];

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function getToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function formatOwnerLabel(row: AgentClientRow): string {
  if (row.ownerType === 'agent' && row.ownerUser) return `${row.ownerUser.name} (агент)`;
  return 'Админ';
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

function mapSummaryItemToClientRow(it: AdminClientSummaryItem): AgentClientRow {
  const profile = it.profile;
  const displayName = profile?.name?.trim() || it.user.name?.trim() || it.user.login;
  const row: AgentClientRow = {
    id: it.user.id,
    name: displayName,
    login: it.user.login,
    ownerType: it.ownerType,
    ownerUser: it.ownerUser
      ? { id: it.ownerUser.id, name: it.ownerUser.name || it.ownerUser.login, login: it.ownerUser.login }
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
    pendingChanges: it.pendingChanges ?? 0,
    pendingCreates: it.pendingCreates ?? 0,
    pendingBlacklistAdds: 0,
    pendingBlacklistDeletes: 0,
    autoLimitControlEnabled: Boolean(it.autoLimitControlEnabled),
    telegramNotificationsChatId: it.user.telegramNotificationsChatId ?? null,
    telegramAutoPauseEnabled: Boolean(it.user.telegramAutoPauseEnabled),
    uniqueProjectNamesEnabled: Boolean(it.user.uniqueProjectNamesEnabled),
    internalClientId: profile?.internalClientId ?? null,
    tableUrl: profile?.tableUrl ?? null,
    inn: profile?.inn,
    phone: profile?.phone,
    contact: profile?.contact,
  };
  return row;
}

function AdminAgentsScreen({
  onOpenClientProjects,
  onOpenClientChanges,
  onOpenClientBlacklistChanges,
  onOpenClientBalance,
}: AdminAgentsScreenProps) {
  const env = import.meta.env as Record<string, unknown>;
  const [agents, setAgents] = useState<AdminAgentSummaryItem[]>([]);
  const [agentClients, setAgentClients] = useState<AgentClientRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedAgentId, setExpandedAgentId] = useState<number | null>(null);
  const [selectedClientId, setSelectedClientId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [agentsPage, setAgentsPage] = useState(1);
  const [agentsPageSize, setAgentsPageSize] = useState(25);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [range, setRange] = useState<DateRange>(() => ({ from: getToday(), to: getToday() }));
  const [createAgentOpen, setCreateAgentOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<AdminAgentSummaryItem | null>(null);
  const [cardClientId, setCardClientId] = useState<number | null>(null);
  const [openingAgentCabinetId, setOpeningAgentCabinetId] = useState<number | null>(null);
  const [tariffModalState, setTariffModalState] = useState<{
    clientId: number;
    clientName: string;
    mode: 'create';
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
    login: string;
  } | null>(null);
  const [openingClientCabinetId, setOpeningClientCabinetId] = useState<number | null>(null);
  const [savingWorkStatusClientId, setSavingWorkStatusClientId] = useState<number | null>(null);
  const [collectionState, setCollectionState] = useState<AdminClientCollectionState | null>(null);
  const [collectionLoading, setCollectionLoading] = useState(false);
  const [collectionActionLoading, setCollectionActionLoading] = useState(false);
  const [collectionRunInfo, setCollectionRunInfo] = useState<{ mode: 'pause' | 'resume'; total: number } | null>(null);
  const [collectionLastInfo, setCollectionLastInfo] = useState<string | null>(null);
  const [pauseSnapshotExpanded, setPauseSnapshotExpanded] = useState(false);
  const [ownerTarget, setOwnerTarget] = useState<string>('admin');
  const [isMobile, setIsMobile] = useState(false);
  const clientCabinetBase =
    typeof env.VITE_CLIENT_PORTAL_URL === 'string' && env.VITE_CLIENT_PORTAL_URL
      ? (env.VITE_CLIENT_PORTAL_URL as string)
      : '/';

  const expandedClients = useMemo(
    () => agentClients.filter((client) => client.ownerType === 'agent' && client.ownerUser?.id === expandedAgentId),
    [agentClients, expandedAgentId],
  );
  const totalAgents = agents.length;
  const agentsTotalPages = Math.max(1, Math.ceil(totalAgents / agentsPageSize));
  const agentsPageSafe = Math.min(Math.max(agentsPage, 1), agentsTotalPages);
  const agentsStart = (agentsPageSafe - 1) * agentsPageSize;
  const agentsEnd = agentsStart + agentsPageSize;
  const agentsPageRows = agents.slice(agentsStart, agentsEnd);
  const filteredExpandedClients = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return expandedClients;
    return expandedClients.filter((client) =>
      client.name.toLowerCase().includes(q)
      || client.login.toLowerCase().includes(q)
      || String(client.id).includes(q),
    );
  }, [expandedClients, search]);
  const totalExpandedClients = filteredExpandedClients.length;
  const totalPages = Math.max(1, Math.ceil(totalExpandedClients / pageSize));
  const pageSafe = Math.min(Math.max(page, 1), totalPages);
  const start = (pageSafe - 1) * pageSize;
  const end = start + pageSize;
  const pageRows = filteredExpandedClients.slice(start, end);
  const selectedClient = useMemo(
    () => (selectedClientId != null ? expandedClients.find((client) => client.id === selectedClientId) ?? null : null),
    [expandedClients, selectedClientId],
  );
  const selectedExpandedAgent = useMemo(
    () => (expandedAgentId != null ? agents.find((agent) => agent.user.id === expandedAgentId) ?? null : null),
    [agents, expandedAgentId],
  );
  const selectedPendingTotal =
    selectedClient != null
      ? (selectedClient.pendingChanges ?? 0)
        + (selectedClient.pendingCreates ?? 0)
        + (selectedClient.pendingBlacklistAdds ?? 0)
        + (selectedClient.pendingBlacklistDeletes ?? 0)
      : 0;
  const selectedBlacklistTotal =
    selectedClient != null
      ? (selectedClient.pendingBlacklistAdds ?? 0) + (selectedClient.pendingBlacklistDeletes ?? 0)
      : 0;
  const selectedAccrued = selectedClient != null ? selectedClient.remaining + selectedClient.usedTotal : 0;

  async function loadData(preferredExpandedAgentId?: number | null, preferredClientId?: number | null) {
    try {
      setLoading(true);
      setError(null);
      const [agentsResp, clientsResp] = await Promise.all([
        fetchAdminAgents(),
        fetchAdminClientsSummary({ fromDate: range.from, toDate: range.to }),
      ]);
      const nextAgentClients = clientsResp.items.filter((item) => item.ownerType === 'agent').map(mapSummaryItemToClientRow);

      setAgents(agentsResp.items);
      setAgentClients(nextAgentClients);

      const requestedExpandedAgentId = preferredExpandedAgentId === undefined ? expandedAgentId : preferredExpandedAgentId;
      const nextExpandedAgentId =
        requestedExpandedAgentId != null && agentsResp.items.some((agent) => agent.user.id === requestedExpandedAgentId)
          ? requestedExpandedAgentId
          : null;
      setExpandedAgentId(nextExpandedAgentId);

      const nextExpandedClients = nextAgentClients.filter(
        (client) => client.ownerType === 'agent' && client.ownerUser?.id === nextExpandedAgentId,
      );
      if (nextExpandedAgentId == null) {
        setSelectedClientId(null);
      } else if (preferredClientId != null && nextExpandedClients.some((client) => client.id === preferredClientId)) {
        setSelectedClientId(preferredClientId);
      } else if (selectedClientId != null && nextExpandedClients.some((client) => client.id === selectedClientId)) {
        setSelectedClientId(selectedClientId);
      } else {
        setSelectedClientId(null);
      }
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить агентов'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadData(expandedAgentId, selectedClientId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  useEffect(() => {
    setPage(1);
  }, [search, expandedAgentId]);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 768px)');
    const handleChange = () => setIsMobile(media.matches);
    handleChange();
    media.addEventListener('change', handleChange);
    return () => {
      media.removeEventListener('change', handleChange);
    };
  }, []);

  useEffect(() => {
    if (expandedAgentId == null) return;
    if (agentsPageRows.some((agent) => agent.user.id === expandedAgentId)) return;
    setExpandedAgentId(null);
    setSelectedClientId(null);
  }, [agentsPageRows, expandedAgentId]);

  useEffect(() => {
    if (filteredExpandedClients.length === 0) {
      setSelectedClientId(null);
      return;
    }
    if (selectedClientId == null) {
      return;
    }
    if (!expandedClients.some((client) => client.id === selectedClientId)) {
      setSelectedClientId(null);
      return;
    }
    if (!filteredExpandedClients.some((client) => client.id === selectedClientId)) {
      setSelectedClientId(null);
      return;
    }
    if (!pageRows.some((client) => client.id === selectedClientId)) {
      setSelectedClientId(null);
    }
  }, [expandedClients, filteredExpandedClients, pageRows, selectedClientId]);

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

  useEffect(() => {
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
  }, [selectedClientId]);

  useEffect(() => {
    setCollectionLastInfo(null);
    setCollectionRunInfo(null);
    setPauseSnapshotExpanded(false);
  }, [selectedClientId]);

  function toggleAgent(agentId: number) {
    setSearch('');
    setPage(1);
    if (expandedAgentId === agentId) {
      setExpandedAgentId(null);
      setSelectedClientId(null);
      return;
    }
    setExpandedAgentId(agentId);
    setSelectedClientId(null);
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

  async function handleOpenAgentCabinet(agentId: number) {
    if (!agentId) return;
    setOpeningAgentCabinetId(agentId);
    setError(null);
    try {
      const resp = await impersonateAgent(agentId);
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
      setError(getErrorMessage(err, 'Не удалось открыть ЛК агента'));
      setOpeningAgentCabinetId(null);
    }
  }

  async function handleTransferOwner() {
    if (!selectedClient) return;
    const payload =
      ownerTarget === 'admin'
        ? { ownerType: 'admin' as const }
        : { ownerType: 'agent' as const, agentId: Number(ownerTarget.split(':')[1]) };
    try {
      await transferAdminClientOwner(selectedClient.id, payload);
      window.dispatchEvent(new CustomEvent('app-toast', { detail: 'Владелец клиента обновлён' }));
      await loadData(payload.ownerType === 'agent' ? payload.agentId ?? null : expandedAgentId, selectedClient.id);
    } catch (err: unknown) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось переназначить клиента') }));
    }
  }

  async function handleToggleCollection() {
    if (!selectedClient) return;
    if (!collectionState) return;

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
      setCollectionState(resp.state);
      const successCount = isPause ? resp.pausedCount : resp.resumedCount;
      const processedCount = successCount + resp.skippedCount + resp.failedCount;
      setCollectionLastInfo(
        `Выполнено: ${successCount}/${processedCount || 0}. Пропущено: ${resp.skippedCount}. Ошибок: ${resp.failedCount}.`,
      );

      const lines: string[] = [resp.message];
      if (resp.failedCount > 0 || resp.skippedCount > 0) {
        lines.push(
          `Детали: успешно ${isPause ? resp.pausedCount : resp.resumedCount}, пропущено ${resp.skippedCount}, ошибок ${resp.failedCount}.`,
        );
      }
      if (resp.errors.length > 0) {
        lines.push(resp.errors.slice(0, 5).join('\n'));
        if (resp.errors.length > 5) {
          lines.push(`... и ещё ${resp.errors.length - 5}`);
        }
      }
      window.dispatchEvent(new CustomEvent('app-toast', { detail: lines.join('\n') }));
    } catch (err: unknown) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось изменить режим сбора данных') }));
    } finally {
      setCollectionActionLoading(false);
      setCollectionRunInfo(null);
    }
  }

  async function handleToggleAutoLimitControl() {
    if (!selectedClient) return;
    const nextEnabled = !selectedClient.autoLimitControlEnabled;
    const confirmText = nextEnabled
      ? 'Включить авто-контроль лимитов для этого клиента?'
      : 'Выключить авто-контроль лимитов для этого клиента? После этого управление будет полностью ручным.';
    if (!window.confirm(confirmText)) return;
    try {
      await updateAdminClient(selectedClient.id, { autoLimitControlEnabled: nextEnabled });
      setAgentClients((prev) =>
        prev.map((row) =>
          row.id === selectedClient.id
            ? {
                ...row,
                autoLimitControlEnabled: nextEnabled,
              }
            : row,
        ),
      );
      window.dispatchEvent(
        new CustomEvent('app-toast', {
          detail: nextEnabled
            ? 'Авто-контроль лимитов включён.'
            : 'Авто-контроль лимитов выключен. Управление проектами полностью ручное.',
        }),
      );
    } catch (err: unknown) {
      window.dispatchEvent(
        new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось изменить режим авто-контроля лимитов') }),
      );
    }
  }

  async function handleWorkStatusChange(clientId: number, nextStatus: ClientWorkStatus) {
    const prevRow = agentClients.find((row) => row.id === clientId);
    if (!prevRow || prevRow.workStatus === nextStatus) return;
    setSavingWorkStatusClientId(clientId);
    setAgentClients((prev) =>
      prev.map((row) => (row.id === clientId ? { ...row, workStatus: nextStatus } : row)),
    );
    try {
      const resp = await updateAdminClientWorkStatus(clientId, nextStatus);
      setAgentClients((prev) =>
        prev.map((row) => (row.id === clientId ? { ...row, workStatus: resp.workStatus } : row)),
      );
    } catch (err: unknown) {
      setAgentClients((prev) =>
        prev.map((row) => (row.id === clientId ? { ...row, workStatus: prevRow.workStatus } : row)),
      );
      window.dispatchEvent(
        new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось изменить рабочий статус клиента') }),
      );
    } finally {
      setSavingWorkStatusClientId(null);
    }
  }

  function openTariffModal(client: Pick<AgentClientRow, 'id' | 'name'>) {
    setTariffModalState({
      clientId: client.id,
      clientName: client.name,
      mode: 'create',
    });
  }

  function renderExpandedAgentContent() {
    return (
      <>
        <div className="table-card agent-accordion__panel">
          <div className="table-toolbar toolbar-split">
            <div className="filters toolbar-left">
              <DateRangeFilter
                from={range.from}
                to={range.to}
                onChange={(next) => {
                  setPage(1);
                  setRange(next);
                }}
              />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Поиск по имени / ID клиента"
                style={{ minWidth: 220, flex: 1 }}
              />
            </div>
          </div>
          <div className="table-footer table-footer--top">
            Показано {pageRows.length} из {totalExpandedClients}
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
                  <th>ID</th>
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
                {totalExpandedClients === 0 && (
                  <tr>
                    <td colSpan={9} className="muted" style={{ padding: 16 }}>
                      У агента пока нет клиентов по текущему фильтру.
                    </td>
                  </tr>
                )}
                {pageRows.map((client) => {
                  const blacklistTotal = (client.pendingBlacklistAdds ?? 0) + (client.pendingBlacklistDeletes ?? 0);
                  const hasEvents = (client.pendingChanges ?? 0) > 0 || (client.pendingCreates ?? 0) > 0 || blacklistTotal > 0;
                  const isDebt = client.remaining < 0;
                  return (
                    <tr
                      key={client.id}
                      className={`client-row${isDebt ? ' row--debt' : ''}`}
                      style={{
                        cursor: 'pointer',
                        backgroundColor: selectedClientId === client.id ? '#f7f8fc' : undefined,
                      }}
                      onClick={() => {
                        setSelectedClientId((prev) => (prev === client.id ? null : client.id));
                      }}
                    >
                      <td className="muted">{client.id}</td>
                      <td>
                        <div className="name">{client.name}</div>
                        {hasEvents && (
                          <div className="chip-stack">
                            {client.pendingChanges > 0 && (
                              <span className="badge badge--orange">Изменения: {client.pendingChanges}</span>
                            )}
                            {client.pendingCreates > 0 && (
                              <span className="badge badge--gray">Создания: {client.pendingCreates}</span>
                            )}
                          </div>
                        )}
                      </td>
                      <td>{client.projectCount}</td>
                      <td>
                        <span className={getCollectionBadgeClass(client.dataCollectionStatus)}>
                          {client.dataCollectionStatus}
                        </span>
                      </td>
                      <td>{client.tariffAmount == null ? '-' : client.tariffAmount}</td>
                      <td>
                        <div className={isDebt ? 'remaining-negative' : undefined}>{client.remaining}</div>
                        {client.financeStatus && (
                          <div className="client-finance-badge-row">
                            <span className={getFinanceBadgeClass(client.financeStatus)}>{client.financeStatus}</span>
                          </div>
                        )}
                      </td>
                      <td>
                        <select
                          className={getWorkStatusSelectClass(client.workStatus)}
                          value={client.workStatus}
                          disabled={savingWorkStatusClientId === client.id}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => {
                            e.stopPropagation();
                            void handleWorkStatusChange(client.id, e.target.value as ClientWorkStatus);
                          }}
                        >
                          {WORK_STATUSES.map((statusOption) => (
                            <option key={statusOption} value={statusOption}>
                              {statusOption}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>{client.totalVolume}</td>
                      <td>
                        <div className="agent-client-row__actions">
                          <button
                            className="btn btn--primary"
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              openTariffModal(client);
                            }}
                          >
                            Тариф
                          </button>
                          <button
                            className="btn btn--primary"
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setCardClientId(client.id);
                              setCardClientData({
                                name: client.name,
                                inn: client.inn,
                                phone: client.phone,
                                contact: client.contact,
                                telegramNotificationsChatId: client.telegramNotificationsChatId,
                                telegramAutoPauseEnabled: client.telegramAutoPauseEnabled,
                                uniqueProjectNamesEnabled: client.uniqueProjectNamesEnabled,
                                internalClientId: client.internalClientId,
                                tableUrl: client.tableUrl,
                                login: client.login,
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
                              void handleOpenClientCabinet(client.id);
                            }}
                            disabled={openingClientCabinetId === client.id}
                          >
                            {openingClientCabinetId === client.id ? 'Переходим…' : 'Перейти в ЛК'}
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
            Показано {pageRows.length} из {totalExpandedClients}
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
        </div>

        {selectedClient ? (
          <div className="table-card client-summary-card agent-accordion__client-summary agent-accordion__client-summary--dense">
            <div className="client-summary">
              <div className="client-summary__header">
                <div className="client-summary__info">
                  <div className="client-summary__title">{selectedClient.name}</div>
                  <div className="client-summary__meta">
                    <span className="sub">ID: {selectedClient.id}</span>
                    {selectedClient.remaining < 0 && <span className="badge badge--orange">Долг</span>}
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
                          <button type="button" className="btn btn--secondary" onClick={() => onOpenClientChanges(selectedClient.id, selectedClient.name)}>
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
                          <button type="button" className="btn btn--secondary" onClick={() => onOpenClientBlacklistChanges(selectedClient.id, selectedClient.name)}>
                            События ЧС
                            {selectedBlacklistTotal > 0 && (
                              <span className="btn__meta">
                                <span className="badge badge--orange">ЧС: {selectedBlacklistTotal}</span>
                              </span>
                            )}
                          </button>
                        )}
                        {onOpenClientProjects && (
                          <button type="button" className="btn btn--secondary" onClick={() => onOpenClientProjects(selectedClient.id, selectedClient.name)}>
                            Перейти к проектам
                          </button>
                        )}
                      </div>
                    </section>

                    <section className="client-summary__section">
                      <div className="client-summary__section-title">Финансы</div>
                      <div className="client-summary__section-actions">
                        {onOpenClientBalance && (
                          <button type="button" className="btn btn--primary" onClick={() => onOpenClientBalance(selectedClient.id, selectedClient.name, 'tariff')}>
                            Управление тарифами
                          </button>
                        )}
                      </div>
                    </section>

                    <section className="client-summary__section">
                      <div className="client-summary__section-title">Владелец</div>
                      <div className="client-summary__section-actions agent-accordion__owner-actions">
                        <div className="sub">
                          Текущий владелец: <b>{formatOwnerLabel(selectedClient)}</b>
                        </div>
                        <select className="agent-accordion__owner-select" value={ownerTarget} onChange={(e) => setOwnerTarget(e.target.value)}>
                          <option value="admin">Админ</option>
                          {agents
                            .filter((item) => !item.user.isDisabled || ownerTarget === `agent:${item.user.id}`)
                            .map((item) => (
                              <option key={item.user.id} value={`agent:${item.user.id}`}>
                                {item.user.name || item.user.login} ({item.user.login})
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
                      </div>
                    </section>
                  </div>

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
                          || collectionActionLoading
                          || !collectionState
                          || !collectionState.actionEnabled
                        }
                        onClick={() => {
                          void handleToggleCollection();
                        }}
                        title={collectionState?.actionDisabledReason || undefined}
                      >
                        <span>
                          {collectionActionLoading
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
                      {collectionActionLoading && collectionRunInfo && (
                        <span className="sub">
                          {collectionRunInfo.mode === 'pause' ? 'Обрабатываем паузу' : 'Обрабатываем восстановление'}
                          {collectionRunInfo.total > 0 ? `: 0/${collectionRunInfo.total}` : '...'}
                        </span>
                      )}
                      {!collectionActionLoading && !!collectionLastInfo && !/Выполнено:\s*0\/0\.\s*Пропущено:\s*0\.\s*Ошибок:\s*0\./.test(collectionLastInfo) && (
                        <span className="sub">{collectionLastInfo}</span>
                      )}
                    </div>
                  </section>
                </div>
              </div>
            </div>

            <div className="summary-grid">
              <div className="summary-card">
                <div className="sub">Проектов</div>
                <div className="value">{selectedClient.projectCount}</div>
              </div>
              <div className="summary-card">
                <div className="sub">Данных за период</div>
                <div className="value">{selectedClient.totalVolume}</div>
              </div>
              <div className="summary-card">
                <div className="sub">Тариф</div>
                <div className="value">{selectedClient.tariffAmount == null ? '-' : selectedClient.tariffAmount}</div>
              </div>
              <div className="summary-card">
                <div className="sub">Баланс</div>
                <div className={`value${selectedClient.remaining < 0 ? ' value--negative' : ''}`}>Остаток: {selectedClient.remaining}</div>
                <div className="sub">Использовано: {selectedClient.usedTotal}</div>
                <div className="sub">Начислено: {selectedAccrued}</div>
              </div>
            </div>

            <div className="agent-accordion__snapshot">
              <button
                type="button"
                className="btn btn--ghost agent-accordion__snapshot-toggle"
                onClick={() => setPauseSnapshotExpanded((prev) => !prev)}
                title={pauseSnapshotExpanded ? 'Свернуть список' : 'Развернуть список'}
              >
                <span className="agent-accordion__snapshot-summary">
                  <span
                    className="sub agent-accordion__snapshot-icon"
                  >
                    i
                  </span>
                  <span>
                    Проекты из последней массовой паузы
                    {collectionState ? ` (${collectionState.snapshotProjects.length})` : ''}
                  </span>
                </span>
                <span className="sub agent-accordion__snapshot-caret">
                  {pauseSnapshotExpanded ? '▾' : '▸'}
                </span>
              </button>

              {pauseSnapshotExpanded && (
                <div className="agent-accordion__snapshot-list">
                  {collectionLoading && <div className="sub">Загрузка списка…</div>}
                  {!collectionLoading && (!collectionState || collectionState.snapshotProjects.length === 0) && (
                    <div className="sub">Снимок отсутствует.</div>
                  )}
                  {!collectionLoading && collectionState && collectionState.snapshotProjects.length > 0 && (
                    <div className="agent-accordion__snapshot-projects">
                      {collectionState.snapshotProjects.map((project) => (
                        <div
                          key={project.id}
                          className="agent-accordion__snapshot-project"
                        >
                          <span>{formatProjectNameForDisplay(project.name)} (id: {project.id})</span>
                          <span
                            className={
                              project.status === 'Активен'
                                ? 'badge badge--green'
                                : project.status === 'На паузе'
                                  ? 'badge badge--orange'
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
          </div>
        ) : (
          <div className="table-card agent-accordion__placeholder">
            <div style={{ fontSize: '1.05rem', fontWeight: 600 }}>Клиент не выбран</div>
            <div className="sub agent-accordion__placeholder-text">
              Выберите клиента в таблице выше, чтобы открыть detail-режим.
            </div>
          </div>
        )}
      </>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {isMobile && selectedExpandedAgent ? (
        <div className="table-card agent-mobile-view">
          <div className="agent-mobile-view__header">
            <button
              type="button"
              className="btn btn--ghost agent-mobile-view__back"
              onClick={() => toggleAgent(selectedExpandedAgent.user.id)}
            >
              ← К списку агентов
            </button>
            <div className="agent-mobile-view__identity">
              <div className="agent-mobile-view__title">{selectedExpandedAgent.user.name || selectedExpandedAgent.user.login}</div>
              <div className="sub">{selectedExpandedAgent.user.login} (id: {selectedExpandedAgent.user.id})</div>
            </div>
            <div className="agent-mobile-view__actions">
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => setEditingAgent(selectedExpandedAgent)}
              >
                Редактировать
              </button>
              <button
                type="button"
                className="btn btn--secondary"
                onClick={() => {
                  void handleOpenAgentCabinet(selectedExpandedAgent.user.id);
                }}
                disabled={openingAgentCabinetId === selectedExpandedAgent.user.id}
              >
                {openingAgentCabinetId === selectedExpandedAgent.user.id ? 'Переходим…' : 'Перейти в ЛК'}
              </button>
            </div>
          </div>
          <div className="agent-mobile-view__content">
            {renderExpandedAgentContent()}
          </div>
        </div>
      ) : (
      <div className="table-card">
        <div className="table-toolbar toolbar-split">
          <div className="filters toolbar-left">
            <span className="toolbar-meta">Всего агентов: {agents.length}</span>
          </div>
          <div className="actions toolbar-right">
            <button type="button" className="btn btn--primary" onClick={() => setCreateAgentOpen(true)}>
              + Новый агент
            </button>
          </div>
        </div>
        {error && <div className="sub" style={{ color: '#d00', padding: '0 16px 12px' }}>{error}</div>}
        <div className="table-footer table-footer--top">
          Показано {agentsPageRows.length} из {totalAgents}
          <div className="spacer" />
          <div className="pager">
            <button
              className="pager__btn"
              disabled={agentsPageSafe <= 1}
              onClick={() => {
                const p = Math.max(1, agentsPageSafe - 1);
                setAgentsPage(p);
              }}
            >
              ‹
            </button>
            <span className="pager__info">
              {agentsPageSafe} / {agentsTotalPages}
            </span>
            <button
              className="pager__btn"
              disabled={agentsPageSafe >= agentsTotalPages}
              onClick={() => {
                const p = Math.min(agentsTotalPages, agentsPageSafe + 1);
                setAgentsPage(p);
              }}
            >
              ›
            </button>
            <select
              className="pager__size"
              value={agentsPageSize}
              onChange={(e) => {
                const s = Number(e.target.value);
                setAgentsPageSize(s);
                setAgentsPage(1);
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
                <th>Агент</th>
                <th>Статус</th>
                <th>Остаток клиентов</th>
                <th>Клиентов</th>
                <th>Создан</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {!loading && agents.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted" style={{ padding: 16 }}>Агенты пока не созданы.</td>
                </tr>
              )}
              {agentsPageRows.map((agent) => {
                const isExpanded = expandedAgentId === agent.user.id;
                return (
                  <Fragment key={agent.user.id}>
                    <tr
                      style={{ backgroundColor: isExpanded ? '#f7f8fc' : undefined, cursor: 'pointer' }}
                      onClick={() => toggleAgent(agent.user.id)}
                    >
                      <td>
                        <div className="name">{agent.user.name || agent.user.login}</div>
                        <div className="sub">{agent.user.login} (id: {agent.user.id})</div>
                      </td>
                      <td>
                        <span className={agent.user.isDisabled ? 'badge badge--orange' : 'badge badge--green'}>
                          {agent.user.isDisabled ? 'Отключён' : 'Активен'}
                        </span>
                      </td>
                      <td>{agent.balance}</td>
                      <td>{agent.clientCount}</td>
                      <td className="muted"><DateTimeCompact value={agent.createdAt} /></td>
                      <td>
                        <div className="agent-row__actions">
                          <button
                            type="button"
                            className="btn btn--secondary"
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleAgent(agent.user.id);
                            }}
                          >
                            {isExpanded ? 'Свернуть' : 'Клиенты'}
                          </button>
                              <button
                                type="button"
                                className="btn btn--ghost"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setEditingAgent(agent);
                                }}
                              >
                                Редактировать
                          </button>
                          <button
                            type="button"
                            className="btn btn--secondary"
                            onClick={(e) => {
                              e.stopPropagation();
                              void handleOpenAgentCabinet(agent.user.id);
                            }}
                            disabled={openingAgentCabinetId === agent.user.id}
                          >
                            {openingAgentCabinetId === agent.user.id ? 'Переходим…' : 'Перейти в ЛК'}
                          </button>
                        </div>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr>
                        <td colSpan={6} className="agent-accordion__cell">
                          <div className="agent-accordion__body">
                            <div className="agent-accordion__content">
                              <div className="agent-accordion__header">
                                <div className="agent-accordion__identity">
                                  <div className="agent-accordion__title">{agent.user.name || agent.user.login}</div>
                                  <div className="sub">{agent.user.login} (id: {agent.user.id})</div>
                                </div>
                              </div>

                              <div className="table-card agent-accordion__panel">
                                <div className="table-toolbar toolbar-split">
                                  <div className="filters toolbar-left">
                                    <DateRangeFilter
                                      from={range.from}
                                      to={range.to}
                                      onChange={(next) => {
                                        setPage(1);
                                        setRange(next);
                                      }}
                                    />
                                    <input
                                      type="search"
                                      value={search}
                                      onChange={(e) => setSearch(e.target.value)}
                                      placeholder="Поиск по имени / ID клиента"
                                      style={{ minWidth: 220, flex: 1 }}
                                    />
                                  </div>
                                </div>
                                <div className="table-footer table-footer--top">
                                  Показано {pageRows.length} из {totalExpandedClients}
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
                                        <th>ID</th>
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
                                      {totalExpandedClients === 0 && (
                                        <tr>
                                          <td colSpan={9} className="muted" style={{ padding: 16 }}>
                                            У агента пока нет клиентов по текущему фильтру.
                                          </td>
                                        </tr>
                                      )}
                                      {pageRows.map((client) => {
                                        const blacklistTotal = (client.pendingBlacklistAdds ?? 0) + (client.pendingBlacklistDeletes ?? 0);
                                        const hasEvents = (client.pendingChanges ?? 0) > 0 || (client.pendingCreates ?? 0) > 0 || blacklistTotal > 0;
                                        const isDebt = client.remaining < 0;
                                        return (
                                          <tr
                                            key={client.id}
                                            className={`client-row${isDebt ? ' row--debt' : ''}`}
                                            style={{
                                              cursor: 'pointer',
                                              backgroundColor: selectedClientId === client.id ? '#f7f8fc' : undefined,
                                            }}
                                            onClick={() => {
                                              setSelectedClientId((prev) => (prev === client.id ? null : client.id));
                                            }}
                                          >
                                            <td className="muted">{client.id}</td>
                                            <td>
                                              <div className="name">{client.name}</div>
                                              {hasEvents && (
                                                <div className="chip-stack">
                                                  {client.pendingChanges > 0 && (
                                                    <span className="badge badge--orange">Изменения: {client.pendingChanges}</span>
                                                  )}
                                                  {client.pendingCreates > 0 && (
                                                    <span className="badge badge--gray">Создания: {client.pendingCreates}</span>
                                                  )}
                                                </div>
                                              )}
                                            </td>
                                            <td>{client.projectCount}</td>
                                            <td>
                                              <span className={getCollectionBadgeClass(client.dataCollectionStatus)}>
                                                {client.dataCollectionStatus}
                                              </span>
                                            </td>
                                            <td>{client.tariffAmount == null ? '-' : client.tariffAmount}</td>
                                            <td>
                                              <div className={isDebt ? 'remaining-negative' : undefined}>{client.remaining}</div>
                                              {client.financeStatus && (
                                                <div className="client-finance-badge-row">
                                                  <span className={getFinanceBadgeClass(client.financeStatus)}>{client.financeStatus}</span>
                                                </div>
                                              )}
                                            </td>
                                            <td>
                                              <select
                                                className={getWorkStatusSelectClass(client.workStatus)}
                                                value={client.workStatus}
                                                disabled={savingWorkStatusClientId === client.id}
                                                onClick={(e) => e.stopPropagation()}
                                                onChange={(e) => {
                                                  e.stopPropagation();
                                                  void handleWorkStatusChange(client.id, e.target.value as ClientWorkStatus);
                                                }}
                                              >
                                                {WORK_STATUSES.map((statusOption) => (
                                                  <option key={statusOption} value={statusOption}>
                                                    {statusOption}
                                                  </option>
                                                ))}
                                              </select>
                                            </td>
                                            <td>{client.totalVolume}</td>
                                            <td>
                                              <div className="agent-client-row__actions">
                                                <button
                                                  className="btn btn--primary"
                                                  type="button"
                                                  onClick={(e) => {
                                                    e.stopPropagation();
                                                    openTariffModal(client);
                                                  }}
                                                >
                                                  Тариф
                                                </button>
                                                <button
                                                  className="btn btn--primary"
                                                  type="button"
                                                  onClick={(e) => {
                                                    e.stopPropagation();
                                                    setCardClientId(client.id);
                                                    setCardClientData({
                                                      name: client.name,
                                                      inn: client.inn,
                                                      phone: client.phone,
                                                      contact: client.contact,
                                                      telegramNotificationsChatId: client.telegramNotificationsChatId,
                                                      telegramAutoPauseEnabled: client.telegramAutoPauseEnabled,
                                                      uniqueProjectNamesEnabled: client.uniqueProjectNamesEnabled,
                                                      internalClientId: client.internalClientId,
                                                      tableUrl: client.tableUrl,
                                                      login: client.login,
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
                                                    void handleOpenClientCabinet(client.id);
                                                  }}
                                                  disabled={openingClientCabinetId === client.id}
                                                >
                                                  {openingClientCabinetId === client.id ? 'Переходим…' : 'Перейти в ЛК'}
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
                                  Показано {pageRows.length} из {totalExpandedClients}
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
                              </div>

                              {selectedClient ? (
                                <div className="table-card client-summary-card agent-accordion__client-summary agent-accordion__client-summary--dense">
                                  <div className="client-summary">
                                    <div className="client-summary__header">
                                      <div className="client-summary__info">
                                        <div className="client-summary__title">{selectedClient.name}</div>
                                        <div className="client-summary__meta">
                                          <span className="sub">ID: {selectedClient.id}</span>
                                          {selectedClient.remaining < 0 && <span className="badge badge--orange">Долг</span>}
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
                                              <button type="button" className="btn btn--secondary" onClick={() => onOpenClientChanges(selectedClient.id, selectedClient.name)}>
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
                                              <button type="button" className="btn btn--secondary" onClick={() => onOpenClientBlacklistChanges(selectedClient.id, selectedClient.name)}>
                                                События ЧС
                                                {selectedBlacklistTotal > 0 && (
                                                  <span className="btn__meta">
                                                    <span className="badge badge--orange">ЧС: {selectedBlacklistTotal}</span>
                                                  </span>
                                                )}
                                              </button>
                                            )}
                                            {onOpenClientProjects && (
                                              <button type="button" className="btn btn--secondary" onClick={() => onOpenClientProjects(selectedClient.id, selectedClient.name)}>
                                                Перейти к проектам
                                              </button>
                                            )}
                                          </div>
                                        </section>

                                        <section className="client-summary__section">
                                          <div className="client-summary__section-title">Финансы</div>
                                          <div className="client-summary__section-actions">
                                            {onOpenClientBalance && (
                                              <button type="button" className="btn btn--primary" onClick={() => onOpenClientBalance(selectedClient.id, selectedClient.name, 'tariff')}>
                                                Управление тарифами
                                              </button>
                                            )}
                                          </div>
                                        </section>

                                        <section className="client-summary__section">
                                          <div className="client-summary__section-title">Владелец</div>
                                          <div className="client-summary__section-actions agent-accordion__owner-actions">
                                            <div className="sub">
                                              Текущий владелец: <b>{formatOwnerLabel(selectedClient)}</b>
                                            </div>
                                            <select className="agent-accordion__owner-select" value={ownerTarget} onChange={(e) => setOwnerTarget(e.target.value)}>
                                              <option value="admin">Админ</option>
                                              {agents
                                                .filter((item) => !item.user.isDisabled || ownerTarget === `agent:${item.user.id}`)
                                                .map((item) => (
                                                  <option key={item.user.id} value={`agent:${item.user.id}`}>
                                                    {item.user.name || item.user.login} ({item.user.login})
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
                                          </div>
                                        </section>
                                      </div>

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
                                              || collectionActionLoading
                                              || !collectionState
                                              || !collectionState.actionEnabled
                                            }
                                            onClick={() => {
                                              void handleToggleCollection();
                                            }}
                                            title={collectionState?.actionDisabledReason || undefined}
                                          >
                                            <span>
                                              {collectionActionLoading
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
                                          {collectionActionLoading && collectionRunInfo && (
                                            <span className="sub">
                                              {collectionRunInfo.mode === 'pause' ? 'Обрабатываем паузу' : 'Обрабатываем восстановление'}
                                              {collectionRunInfo.total > 0 ? `: 0/${collectionRunInfo.total}` : '...'}
                                            </span>
                                          )}
                                          {!collectionActionLoading && !!collectionLastInfo && !/Выполнено:\s*0\/0\.\s*Пропущено:\s*0\.\s*Ошибок:\s*0\./.test(collectionLastInfo) && (
                                            <span className="sub">{collectionLastInfo}</span>
                                          )}
                                        </div>
                                      </section>
                                    </div>
                                  </div>
                                    </div>

                                  <div className="summary-grid">
                                    <div className="summary-card">
                                      <div className="sub">Проектов</div>
                                      <div className="value">{selectedClient.projectCount}</div>
                                    </div>
                                    <div className="summary-card">
                                      <div className="sub">Данных за период</div>
                                      <div className="value">{selectedClient.totalVolume}</div>
                                    </div>
                                    <div className="summary-card">
                                      <div className="sub">Тариф</div>
                                      <div className="value">{selectedClient.tariffAmount == null ? '-' : selectedClient.tariffAmount}</div>
                                    </div>
                                    <div className="summary-card">
                                      <div className="sub">Баланс</div>
                                      <div className={`value${selectedClient.remaining < 0 ? ' value--negative' : ''}`}>Остаток: {selectedClient.remaining}</div>
                                      <div className="sub">Использовано: {selectedClient.usedTotal}</div>
                                      <div className="sub">Начислено: {selectedAccrued}</div>
                                    </div>
                                  </div>

                                  <div className="agent-accordion__snapshot">
                                    <button
                                      type="button"
                                      className="btn btn--ghost agent-accordion__snapshot-toggle"
                                      onClick={() => setPauseSnapshotExpanded((prev) => !prev)}
                                      title={pauseSnapshotExpanded ? 'Свернуть список' : 'Развернуть список'}
                                    >
                                      <span className="agent-accordion__snapshot-summary">
                                        <span
                                          className="sub agent-accordion__snapshot-icon"
                                        >
                                          i
                                        </span>
                                        <span>
                                          Проекты из последней массовой паузы
                                          {collectionState ? ` (${collectionState.snapshotProjects.length})` : ''}
                                        </span>
                                      </span>
                                      <span className="sub agent-accordion__snapshot-caret">
                                        {pauseSnapshotExpanded ? '▾' : '▸'}
                                      </span>
                                    </button>

                                    {pauseSnapshotExpanded && (
                                      <div className="agent-accordion__snapshot-list">
                                        {collectionLoading && <div className="sub">Загрузка списка…</div>}
                                        {!collectionLoading && (!collectionState || collectionState.snapshotProjects.length === 0) && (
                                          <div className="sub">Снимок отсутствует.</div>
                                        )}
                                        {!collectionLoading && collectionState && collectionState.snapshotProjects.length > 0 && (
                                          <div className="agent-accordion__snapshot-projects">
                                            {collectionState.snapshotProjects.map((project) => (
                                              <div
                                                key={project.id}
                                                className="agent-accordion__snapshot-project"
                                              >
                                                <span>{formatProjectNameForDisplay(project.name)} (id: {project.id})</span>
                                                <span
                                                  className={
                                                    project.status === 'Активен'
                                                      ? 'badge badge--green'
                                                      : project.status === 'На паузе'
                                                        ? 'badge badge--orange'
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
                                </div>
                              ) : (
                                <div className="table-card agent-accordion__placeholder">
                                  <div style={{ fontSize: '1.05rem', fontWeight: 600 }}>Клиент не выбран</div>
                                  <div className="sub agent-accordion__placeholder-text">
                                    Выберите клиента в таблице выше, чтобы открыть detail-режим.
                                  </div>
                                </div>
                              )}

                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="table-footer">
          Показано {agentsPageRows.length} из {totalAgents}
          <div className="spacer" />
          <div className="pager">
            <button
              className="pager__btn"
              disabled={agentsPageSafe <= 1}
              onClick={() => {
                const p = Math.max(1, agentsPageSafe - 1);
                setAgentsPage(p);
              }}
            >
              ‹
            </button>
            <span className="pager__info">
              {agentsPageSafe} / {agentsTotalPages}
            </span>
            <button
              className="pager__btn"
              disabled={agentsPageSafe >= agentsTotalPages}
              onClick={() => {
                const p = Math.min(agentsTotalPages, agentsPageSafe + 1);
                setAgentsPage(p);
              }}
            >
              ›
            </button>
            <select
              className="pager__size"
              value={agentsPageSize}
              onChange={(e) => {
                const s = Number(e.target.value);
                setAgentsPageSize(s);
                setAgentsPage(1);
              }}
            >
              <option value={10}>10</option>
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </div>
        </div>
      </div>
      )}

      {createAgentOpen && (
        <AdminCreateAgentModal
          onClose={() => setCreateAgentOpen(false)}
          onCreated={() => {
            void loadData(expandedAgentId, selectedClientId);
          }}
        />
      )}

      {editingAgent && (
        <AdminAgentCardModal
          agent={editingAgent}
          onClose={() => setEditingAgent(null)}
          onUpdated={() => {
            void loadData(editingAgent.user.id, selectedClientId);
          }}
        />
      )}

      {cardClientId && cardClientData && (
        <AdminClientCardModal
          clientId={cardClientId}
          managerRole="admin"
          initialName={cardClientData.name}
          initialInn={cardClientData.inn || undefined}
          initialPhone={cardClientData.phone || undefined}
          initialContact={cardClientData.contact || undefined}
          initialTelegramNotificationsChatId={cardClientData.telegramNotificationsChatId || undefined}
          initialTelegramAutoPauseEnabled={cardClientData.telegramAutoPauseEnabled}
          initialUniqueProjectNamesEnabled={cardClientData.uniqueProjectNamesEnabled}
          initialInternalClientId={cardClientData.internalClientId || undefined}
          initialTableUrl={cardClientData.tableUrl || undefined}
          initialLogin={cardClientData.login}
          onClose={() => setCardClientId(null)}
          onUpdated={() => {
            const keepExpandedAgentId = expandedAgentId;
            const keepSelectedClientId = cardClientId;
            setCardClientId(null);
            void loadData(keepExpandedAgentId, keepSelectedClientId);
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
            void loadData(expandedAgentId, selectedClientId);
          }}
          initialEditorMode="create"
          createOnly
          fetchTariffs={fetchAdminClientTariffs}
          createTariff={createAdminClientTariff}
          updateTariff={updateAdminTariff}
          createTariffOp={createAdminTariffOp}
          fetchTariffOps={fetchAdminTariffOps}
        />
      )}
    </div>
  );
}

export default AdminAgentsScreen;
