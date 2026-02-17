// Экран «Клиенты» для админа: список клиентов с агрегированной статистикой по проектам
// Логика максимально простая и прозрачная, без лишних сущностей.
import { useEffect, useMemo, useState } from 'react';
import {
  fetchAdminClientsSummary,
          fetchAdminChangesSummary,
          impersonateClient,
          type AdminClientSummaryItem,
          type AdminClientChangesSummaryListOut,
          type ClientProfile,
        } from '../api';
import DateRangeFilter from './DateRangeFilter';
import DateRangeCompact from './DateRangeCompact';
import AdminCreateClientModal from './AdminCreateClientModal';
import AdminClientCardModal from './AdminClientCardModal';

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
  onOpenClientProjects?: (clientId: number, clientName: string) => void;
  onOpenClientChanges?: (clientId: number, clientName: string) => void;
  onOpenClientBlacklistChanges?: (clientId: number, clientName: string) => void;
  onOpenClientBalance?: (clientId: number, clientName: string, action: 'credit' | 'debit') => void;
};

type ClientStatus = 'Активен' | 'Нет проектов' | 'Долг' | 'Дожим';

type ClientRow = {
  id: number;
  name: string;
  login: string;
  projectCount: number;
  status: ClientStatus;
  remaining: number;      // Остаток по лимиту
  totalVolume: number;    // Использовано за период
  totalLimit: number;
  usedTotal: number;
  pendingChanges: number;
  pendingCreates: number;
  pendingBlacklistAdds: number;
  pendingBlacklistDeletes: number;
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
  const limit = row.totalLimit || 0;
  if (limit > 0 && row.remaining / limit < 0.15) return 'Дожим';
  return 'Активен';
}

function AdminClientsScreen({
  onOpenClientProjects,
  onOpenClientChanges,
  onOpenClientBlacklistChanges,
  onOpenClientBalance,
}: AdminClientsScreenProps) {
  const env = import.meta.env as Record<string, unknown>;
  const [baseClients, setBaseClients] = useState<ClientRow[]>([]);
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
  const [cardClientData, setCardClientData] = useState<{
    name: string;
    inn?: string | null;
    phone?: string | null;
    contact?: string | null;
    login: string;
  } | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const [summary, combinedSummary] = await Promise.all([
          fetchAdminClientsSummary({ fromDate: range.from, toDate: range.to }),
          fetchAdminChangesSummary({ actions: ['create', 'update', 'delete', 'blacklist_add', 'blacklist_delete'] }).catch(
            () => ({ items: [] } as AdminClientChangesSummaryListOut),
          ),
        ]);
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
            projectCount: it.projectCount,
            remaining: it.remaining,
            totalVolume: it.usedPeriod,
            totalLimit: it.totalLimit,
            usedTotal: it.usedTotal,
            pendingChanges: pendingMap[it.user.id] ?? it.pendingChanges ?? 0,
            pendingCreates: createsMap[it.user.id] ?? it.pendingCreates ?? 0,
            pendingBlacklistAdds: blAddsMap[it.user.id] ?? 0,
            pendingBlacklistDeletes: blDeletesMap[it.user.id] ?? 0,
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

  const clients = useMemo(() => baseClients, [baseClients]);

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
                <th>Остаток</th>
                <th>Общий объём данных за период</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {error && (
                <tr>
                  <td colSpan={7} style={{ color: '#d00', padding: 16 }}>
                    {error}
                  </td>
                </tr>
              )}
              {!error && loading && (
                <tr>
                  <td colSpan={7} className="muted" style={{ padding: 16 }}>
                    Загрузка списка клиентов…
                  </td>
                </tr>
              )}
              {!error && !loading && pageRows.length === 0 && (
                <tr>
                  <td colSpan={7} className="muted" style={{ padding: 16 }}>
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
                        <button
                          className="btn btn--secondary"
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedClientId(row.id);
                          }}
                        >
                          Смотреть
                        </button>
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
            <div>
              <div className="client-summary__title">{selectedClient.name}</div>
              <div className="client-summary__meta">
                <span className="sub">ID: {selectedClient.id}</span>
                {selectedClient.remaining < 0 && <span className="badge badge--orange">Долг</span>}
                <span className="badge badge--gray">Необработанных событий: {selectedPendingTotal}</span>
                <span className="sub" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      backgroundColor: STATUS_COLORS[selectedClient.status],
                    }}
                  />
                  {selectedClient.status}
                </span>
              </div>
            </div>
            <div className="client-summary__actions">
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
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => onOpenClientBalance && onOpenClientBalance(selectedClient.id, selectedClient.name, 'credit')}
              >
                Начислить номера
              </button>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => onOpenClientBalance && onOpenClientBalance(selectedClient.id, selectedClient.name, 'debit')}
              >
                Списать номера
              </button>
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
              <div className="sub">Баланс</div>
              <div className={`value${selectedClient.remaining < 0 ? ' value--negative' : ''}`}>
                Остаток: {selectedClient.remaining}
              </div>
              <div className="sub">Использовано: {selectedClient.usedTotal}</div>
              <div className="sub">Начислено: {selectedAccrued}</div>
            </div>
          </div>
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
        initialName={cardClientData.name}
        initialInn={cardClientData.inn || undefined}
        initialPhone={cardClientData.phone || undefined}
        initialContact={cardClientData.contact || undefined}
        initialLogin={cardClientData.login}
        onClose={() => setCardClientId(null)}
        onUpdated={() => {
          setRefreshKey((x) => x + 1);
          setCardClientId(null);
        }}
      />
    )}
    </>
  );
}

export default AdminClientsScreen;
