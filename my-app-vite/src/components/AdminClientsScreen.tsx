// Экран «Клиенты» для админа: список клиентов с агрегированной статистикой по проектам
// Логика максимально простая и прозрачная, без лишних сущностей.
import { useEffect, useMemo, useState } from 'react';
import {
  fetchAdminClientsSummary,
          fetchAdminChangesSummary,
          type AdminClientSummaryItem,
          type AdminClientChangesSummaryListOut,
          type ClientProfile,
        } from '../api';
import DateRangeFilter from './DateRangeFilter';
import AdminCreateClientModal from './AdminCreateClientModal';
import AdminClientCardModal from './AdminClientCardModal';

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
  inn?: string | null;
  phone?: string | null;
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
  onOpenClientBalance,
}: AdminClientsScreenProps) {
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
          fetchAdminChangesSummary({ actions: ['create', 'update', 'delete'] }).catch(
            () => ({ items: [] } as AdminClientChangesSummaryListOut),
          ),
        ]);
        const pendingMap: Record<number, number> = {};
        const createsMap: Record<number, number> = {};
        combinedSummary.items.forEach((i) => {
          pendingMap[i.user.id] = i.pendingChanges ?? 0;
          createsMap[i.user.id] = i.pendingCreates ?? 0;
        });
        const rows: ClientRow[] = summary.items.map((it: AdminClientSummaryItem) => {
          const profile: ClientProfile | null | undefined = it.profile;
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
            inn: profile?.inn,
            phone: profile?.phone,
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

  return (
    <>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div className="table-card">
        <div
          className="table-toolbar"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
          }}
        >
          <div
            className="filters"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              flex: 1,
              minWidth: 0,
              flexWrap: 'wrap',
            }}
          >
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
          <div
            className="actions"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexShrink: 0,
              justifyContent: 'flex-end',
            }}
          >
            {loading ? (
              <span className="sub">Загрузка…</span>
            ) : (
              <span className="sub">
                Всего клиентов: {clients.length}. Период: {range.from} — {range.to}
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
                pageRows.map((row) => (
                  <tr
                    key={row.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() => {
                      setSelectedClientId(row.id);
                    }}
                  >
                    <td className="muted">{row.id}</td>
                    <td>
                      <div className="name">{row.name}</div>
                      {(row.pendingChanges > 0 || row.pendingCreates > 0) && (
                        <div className="sub" style={{ marginTop: 2 }}>
                      {row.pendingChanges > 0 && (
                        <span
                          className="badge badge--orange"
                          style={{ fontWeight: 500, marginRight: 6 }}
                        >
                          Изменения: {row.pendingChanges}
                        </span>
                      )}
                      {row.pendingCreates > 0 && (
                        <span
                          className="badge badge--gray"
                          style={{ fontWeight: 500 }}
                        >
                          Создания: {row.pendingCreates}
                        </span>
                      )}
                        </div>
                      )}
                    </td>
                    <td>{row.projectCount}</td>
                    <td>
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
                    </td>
                    <td>{row.remaining}</td>
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
                      Открыть
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
                          contact: undefined,
                          login: row.login,
                        });
                      }}
                    >
                      Карточка
                    </button>
                  </div>
                    </td>
                  </tr>
                ))}
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
        <div className="table-card">
          <div
            style={{
              padding: 16,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              borderBottom: '1px solid #eee',
            }}
          >
            <div>
              <div style={{ fontWeight: 600 }}>Сводка по клиенту</div>
              <div className="sub">
                {selectedClient.name} (id: {selectedClient.id})
              </div>
              {(selectedClient.pendingChanges > 0 || selectedClient.pendingCreates > 0) && (
                <div className="sub" style={{ marginTop: 4, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span>
                    Необработанных событий: {selectedClient.pendingChanges + selectedClient.pendingCreates}
                  </span>
                  {selectedClient.pendingChanges > 0 && (
                    <span className="badge badge--orange" style={{ fontWeight: 500 }}>
                      Изменения: {selectedClient.pendingChanges}
                    </span>
                  )}
                  {selectedClient.pendingCreates > 0 && (
                    <span className="badge badge--gray" style={{ fontWeight: 500 }}>
                      Создания: {selectedClient.pendingCreates}
                    </span>
                  )}
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              {onOpenClientChanges && (
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() =>
                    onOpenClientChanges(selectedClient.id, selectedClient.name)
                  }
                >
                  Изменения клиента
                  {(selectedClient.pendingChanges > 0 || selectedClient.pendingCreates > 0) && (
                    <span className="sub" style={{ marginLeft: 8, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                      {selectedClient.pendingChanges > 0 && (
                        <span className="badge badge--orange" style={{ fontWeight: 500 }}>
                          Изм: {selectedClient.pendingChanges}
                        </span>
                      )}
                      {selectedClient.pendingCreates > 0 && (
                        <span className="badge badge--gray" style={{ fontWeight: 500 }}>
                          Созд: {selectedClient.pendingCreates}
                        </span>
                      )}
                    </span>
                  )}
                </button>
              )}
              {onOpenClientProjects && (
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() =>
                    onOpenClientProjects(selectedClient.id, selectedClient.name)
                  }
                >
                  Перейти к проектам клиента
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
                className="btn btn--secondary"
                onClick={() => onOpenClientBalance && onOpenClientBalance(selectedClient.id, selectedClient.name, 'debit')}
              >
                Списать номера
              </button>
            </div>
          </div>
          <div style={{ padding: 16, display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
              <div>
                <div className="sub">ID</div>
                <div>{selectedClient.id}</div>
              </div>
              <div>
                <div className="sub">Название</div>
                <div>{selectedClient.name}</div>
              </div>
              <div>
                <div className="sub">Статус клиента</div>
                <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: '50%',
                      backgroundColor: STATUS_COLORS[selectedClient.status],
                    }}
                  />
                  <span>{selectedClient.status}</span>
                </div>
              </div>
              <div>
                <div className="sub">Кол-во проектов</div>
                <div>{selectedClient.projectCount}</div>
              </div>
              <div>
                <div className="sub">Остаток</div>
                <div style={{ color: selectedClient.remaining < 0 ? '#d23' : undefined, fontWeight: 600 }}>
                  {selectedClient.remaining}
                </div>
                {selectedClient.remaining < 0 && (
                  <div className="sub" style={{ color: '#d23' }}>Долг</div>
                )}
              </div>
              <div>
                <div className="sub">Общий объём данных за период</div>
                <div>{selectedClient.totalVolume}</div>
              </div>
              <div>
                <div className="sub">Израсходовано всего</div>
                <div>{selectedClient.usedTotal}</div>
              </div>
              <div>
                <div className="sub">Начислено номеров</div>
                <div>{(clients.find((c) => c.id === selectedClient.id)?.remaining ?? 0) + selectedClient.usedTotal}</div>
              </div>
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


