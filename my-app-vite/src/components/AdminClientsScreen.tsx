// Экран «Клиенты» для админа: список клиентов с агрегированной статистикой по проектам
// Логика максимально простая и прозрачная, без лишних сущностей.
import { useEffect, useMemo, useState } from 'react';
import {
  fetchAdminClientsSummary,
  fetchAdminChangesSummary,
  fetchAdminUsers,
  impersonateClient,
  fetchAdminClientTariffs,
  fetchAdminTariffOps,
  createAdminClientTariff,
  createAdminTariffOp,
  transferAdminClientOwner,
  updateAdminClient,
  fetchAdminClientCollectionState,
  pauseAdminClientProjects,
  resumeAdminClientProjects,
  type AdminClientSummaryItem,
  type AdminClientChangesSummaryListOut,
  type AdminClientCollectionState,
  type ClientProfile,
} from '../api';
import DateRangeFilter from './DateRangeFilter';
import DateRangeCompact from './DateRangeCompact';
import AdminCreateClientModal from './AdminCreateClientModal';
import AdminClientCardModal from './AdminClientCardModal';
import TariffManagerModal from './TariffManagerModal';

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

type ClientStatus = 'Активен' | 'Нет проектов' | 'Долг' | 'Дожим';

type ClientRow = {
  id: number;
  name: string;
  login: string;
  ownerType: 'admin' | 'agent';
  ownerUser?: { id: number; name: string; login: string } | null;
  projectCount: number;
  status: ClientStatus;
  tariffAmount?: number | null;
  remaining: number;      // Остаток по лимиту
  totalVolume: number;    // Использовано за период
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
  inn?: string | null;
  phone?: string | null;
  contact?: string | null;
};

const STATUS_COLORS: Record<ClientStatus, string> = {
  Активен: '#4CAF50',
  'Нет проектов': '#03A9F4',
  Долг: '#F44336',
  Дожим: '#7E57C2',
};

type DateRange = { from: string; to: string };

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

function deriveStatus(row: ClientRow): ClientStatus {
  if (row.projectCount === 0) return 'Нет проектов';
  if (row.remaining <= 0) return 'Долг';
  const statusBase =
    typeof row.tariffAmount === 'number' && row.tariffAmount > 0
      ? row.tariffAmount
      : row.totalLimit || 0;
  if (statusBase > 0 && row.remaining <= statusBase * 0.3) return 'Дожим';
  return 'Активен';
}

function formatOwnerLabel(row: ClientRow): string {
  if (row.ownerType === 'agent' && row.ownerUser) {
    return `${row.ownerUser.name} (агент)`;
  }
  return 'Админ';
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
  const [pageSize, setPageSize] = useState(25);
  const [selectedClientId, setSelectedClientId] = useState<number | null>(null);
  const [range, setRange] = useState<DateRange>(() => getTodayRange());
  const [createOpen, setCreateOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [cardClientId, setCardClientId] = useState<number | null>(null);
  const [openingClientCabinetId, setOpeningClientCabinetId] = useState<number | null>(null);
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
    login: string;
  } | null>(null);

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
            tariffAmount: it.tariffAmount ?? null,
            remaining: it.remaining,
            totalVolume: it.usedPeriod,
            totalLimit: it.totalLimit,
            usedTotal: it.usedTotal,
            pendingChanges: pendingMap[it.user.id] ?? it.pendingChanges ?? 0,
            pendingCreates: createsMap[it.user.id] ?? it.pendingCreates ?? 0,
            pendingBlacklistAdds: blAddsMap[it.user.id] ?? 0,
            pendingBlacklistDeletes: blDeletesMap[it.user.id] ?? 0,
            autoLimitControlEnabled: Boolean(it.autoLimitControlEnabled),
            telegramNotificationsChatId: it.user.telegramNotificationsChatId ?? null,
            telegramAutoPauseEnabled: Boolean(it.user.telegramAutoPauseEnabled),
            uniqueProjectNamesEnabled: Boolean(it.user.uniqueProjectNamesEnabled),
            inn: profile?.inn,
            phone: profile?.phone,
            contact: profile?.contact,
            status: 'Активен',
          };
          return { ...row, status: deriveStatus(row) };
        });
        setBaseClients(rows);
        setPage(1);
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

  const totals = useMemo(() => {
    let totalRemaining = 0;
    let totalVolume = 0;
    let totalProjects = 0;
    filtered.forEach((c) => {
      totalRemaining += c.remaining;
      totalVolume += c.totalVolume;
      totalProjects += c.projectCount;
    });
    return { totalRemaining, totalVolume, totalProjects };
  }, [filtered]);

  const selectedClient = useMemo(
    () => (selectedClientId != null ? clients.find((c) => c.id === selectedClientId) ?? null : null),
    [clients, selectedClientId],
  );

  useEffect(() => {
    if (selectedClientId == null) return;
    if (clients.some((client) => client.id === selectedClientId)) return;
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

  const clientCabinetBase =
    typeof env.VITE_CLIENT_PORTAL_URL === 'string' && env.VITE_CLIENT_PORTAL_URL
      ? (env.VITE_CLIENT_PORTAL_URL as string)
      : '/';

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

  function openTariffModal(client: Pick<ClientRow, 'id' | 'name'>, mode: 'list' | 'create') {
    setTariffModalState({
      clientId: client.id,
      clientName: client.name,
      mode,
    });
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
                <th>ID клиента</th>
                <th>Название клиента</th>
                <th>Кол-во проектов</th>
                <th>Статус клиента</th>
                <th>Тариф</th>
                <th>Остаток</th>
                <th>Общий объём данных за период</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {error && (
                <tr>
                  <td colSpan={8} style={{ color: '#d00', padding: 16 }}>
                    {error}
                  </td>
                </tr>
              )}
              {!error && loading && (
                <tr>
                  <td colSpan={8} className="muted" style={{ padding: 16 }}>
                    Загрузка списка клиентов…
                  </td>
                </tr>
              )}
              {!error && !loading && pageRows.length === 0 && (
                <tr>
                  <td colSpan={8} className="muted" style={{ padding: 16 }}>
                    Клиенты не найдены.
                  </td>
                </tr>
              )}
              {!error &&
                !loading &&
                pageRows.map((row) => {
                  const blacklistTotal = (row.pendingBlacklistAdds ?? 0) + (row.pendingBlacklistDeletes ?? 0);
                  const hasEvents = (row.pendingChanges ?? 0) > 0 || (row.pendingCreates ?? 0) > 0 || blacklistTotal > 0;
                  const isDebt = row.remaining < 0;
                  return (
                    <tr
                      key={row.id}
                      className={`client-row${isDebt ? ' row--debt' : ''}`}
                      style={{ cursor: 'pointer' }}
                      onClick={() => {
                        setSelectedClientId(row.id);
                      }}
                    >
                    <td className="muted">{row.id}</td>
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
                      {row.projectCount === 0 ? (
                        <span className="badge badge--info">Нет проектов</span>
                      ) : (
                        <span
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 6,
                          }}
                        >
                          <span
                            style={{
                              width: 10,
                              height: 10,
                              borderRadius: '50%',
                              backgroundColor: STATUS_COLORS[row.status],
                            }}
                          />
                          <span className="sub" style={{ whiteSpace: 'nowrap' }}>
                            {row.status}
                          </span>
                        </span>
                      )}
                    </td>
                    <td>{row.tariffAmount == null ? '-' : row.tariffAmount}</td>
                    <td>
                      <div className={isDebt ? 'remaining-negative' : undefined}>{row.remaining}</div>
                      {isDebt && (
                        <div className="sub" style={{ color: '#d23' }}>
                          долг
                        </div>
                      )}
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
                            Создать тариф
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
                          {openingClientCabinetId === row.id ? 'Переходим…' : 'Перейти в ЛК'}
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
                {selectedClient.remaining < 0 && <span className="badge badge--orange">Долг</span>}
                <span className="badge badge--gray">Необработанных событий: {selectedPendingTotal}</span>
              </div>
              <div className="client-summary__status-list">
                <div className="client-summary__status-item">
                  <span className="sub">Статус клиента</span>
                  <span className="client-summary__status-value">
                    <span
                      className="client-summary__status-dot"
                      style={{ backgroundColor: STATUS_COLORS[selectedClient.status] }}
                    />
                    <span>{selectedClient.status}</span>
                  </span>
                </div>
                <div className="client-summary__status-item">
                  <span className="sub">Авто-контроль</span>
                  <span className={selectedClient.autoLimitControlEnabled ? 'badge badge--green' : 'badge badge--gray'}>
                    {selectedClient.autoLimitControlEnabled ? 'Авто + ручной' : 'Ручной'}
                  </span>
                </div>
                {!isAgentManager && (
                  <div className="client-summary__status-item">
                    <span className="sub">Сбор данных</span>
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
                  </div>
                )}
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
                  <div className="client-summary__section-actions" style={{ alignItems: 'stretch' }}>
                    <div className="sub">
                      Текущий владелец: <b>{formatOwnerLabel(selectedClient)}</b>
                    </div>
                    {!isAgentManager && (
                      <>
                        <select
                          value={ownerTarget}
                          onChange={(e) => setOwnerTarget(e.target.value)}
                          style={{ minWidth: 240 }}
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
              )}
            </div>
          </div>
          <div className="summary-grid">
            <div className="summary-card">
              <div className="sub">Проектов</div>
              <div className="value">{selectedClient.projectCount}</div>
            </div>
            <div className="summary-card">
              <div className="sub">Объём за период</div>
              <div className="value">{selectedClient.totalVolume}</div>
            </div>
            <div className="summary-card">
              <div className="sub">Тариф</div>
              <div className="value">{selectedClient.tariffAmount == null ? '-' : selectedClient.tariffAmount}</div>
            </div>
            <div className="summary-card">
              <div className="sub">Баланс</div>
              <div className={`value${selectedClient.remaining < 0 ? ' value--negative' : ''}`}>
                Остаток: {selectedClient.remaining}
              </div>
              <div className="sub">Использовано: {selectedClient.usedTotal}</div>
              <div className="sub">Начислено: {selectedAccrued}</div>
            </div>
          </div>
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
                        <span>{project.name} (id: {project.id})</span>
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
        onCreated={(created) => {
          setRefreshKey((x) => x + 1);
          setCreateOpen(false);
          setSelectedClientId(created.user.id);
        }}
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
        fetchTariffOps={fetchAdminTariffOps}
        createTariffOp={createAdminTariffOp}
      />
    )}
    </>
  );
}

export default AdminClientsScreen;
