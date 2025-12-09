// Экран «Клиенты» для админа: список клиентов с агрегированной статистикой по проектам
// Логика максимально простая и прозрачная, без лишних сущностей.
import { useEffect, useMemo, useState } from 'react';
import {
  fetchAdminUsers,
  fetchAdminProjects,
  fetchAdminChangesSummary,
  type UserInfo,
  type AdminProject,
  type AdminClientChangesSummaryListOut,
} from '../api';
import DateRangeFilter from './DateRangeFilter';

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
};

type ClientStatus =
  | 'Активен'
  | 'На паузе'
  | 'Отключен'
  | 'Нет проектов'
  | 'Долг'
  | 'Дожим';

type ClientRow = {
  id: number;
  name: string;
  projectCount: number;
  status: ClientStatus;
  remaining: number;      // Остаток за выбранный период (демо)
  totalVolume: number;    // Объём данных за выбранный период (демо)
  // Базовые значения за "полный" период (используем для пересчёта при смене дат)
  baseRemaining: number;
  baseTotalVolume: number;
  pendingChanges: number;
};

const STATUS_COLORS: Record<ClientStatus, string> = {
  Активен: '#4CAF50',
  'На паузе': '#FFC107',
  Отключен: '#9E9E9E',
  'Нет проектов': '#03A9F4',
  Долг: '#F44336',
  Дожим: '#7E57C2',
};

type DateRange = {
  from: string;
  to: string;
};

// Вспомогательный хелпер: вернуть диапазон "сегодня"
function getTodayRange(): DateRange {
  const today = new Date().toISOString().slice(0, 10);
  return { from: today, to: today };
}

function groupProjectsByUser(projects: AdminProject[]): Map<number, AdminProject[]> {
  const map = new Map<number, AdminProject[]>();
  projects.forEach((p) => {
    const uid = p.user.id;
    if (!map.has(uid)) {
      map.set(uid, []);
    }
    map.get(uid)!.push(p);
  });
  return map;
}

// Базовый статус по проектам (без учёта биллинга)
function deriveClientStatus(projects: AdminProject[]): ClientStatus {
  if (!projects.length) return 'Нет проектов';
  const anyActive = projects.some((p) => p.status === 'Активен');
  const allPaused = projects.every((p) => p.status === 'На паузе');
  const allDeliveryOff = projects.length > 0 && projects.every((p) => p.deliveryStatus === 'Отключена');

  if (anyActive) return 'Активен';
  if (allPaused) return 'На паузе';
  if (allDeliveryOff) return 'Отключен';

  return 'Активен';
}

// DEMO-логика для статусов «Долг» и «Дожим» без реального биллинга.
// ВАЖНО: это только фронтовая имитация для показа заказчику.
function applyDemoDebtStatus(row: ClientRow): ClientRow {
  // Не трогаем клиентов без проектов и явно отключённых
  if (row.status === 'Нет проектов' || row.status === 'Отключен' || row.status === 'На паузе') {
    return row;
  }
  const limit = row.baseRemaining + row.baseTotalVolume;
  if (!limit) return row;

  const remainingRatio = row.remaining / limit;

  if (row.remaining <= 0) {
    return { ...row, status: 'Долг' };
  }
  if (remainingRatio < 0.15) {
    return { ...row, status: 'Дожим' };
  }
  return row;
}

function buildClientRows(users: UserInfo[], projects: AdminProject[]): ClientRow[] {
  const byUser = groupProjectsByUser(projects);
  return users.map((u) => {
    const list = byUser.get(u.id) ?? [];
    const projectCount = list.length;
    const totalLimit = list.reduce((sum, p) => sum + (p.dataLimit || 0), 0);
    // Реальные данные за выбранный период: numbersPeriod, если нет — numbersTotal
    const totalUsedPeriod = list.reduce(
      (sum, p) => sum + (p.numbersPeriod ?? p.numbersTotal ?? 0),
      0,
    );
    const statusBase = deriveClientStatus(list);
    const remainingBase = Math.max(0, totalLimit - totalUsedPeriod);
    const baseRow: ClientRow = {
      id: u.id,
      name: u.login,
      projectCount,
      status: statusBase,
      remaining: remainingBase,
      totalVolume: totalUsedPeriod,
      baseRemaining: remainingBase,
      baseTotalVolume: totalUsedPeriod,
      pendingChanges: 0,
    };
    return applyDemoDebtStatus(baseRow);
  });
}

function AdminClientsScreen({
  onOpenClientProjects,
  onOpenClientChanges,
}: AdminClientsScreenProps) {
  const [baseClients, setBaseClients] = useState<ClientRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [selectedClientId, setSelectedClientId] = useState<number | null>(null);
  const [range, setRange] = useState<DateRange>(() => getTodayRange());

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const [users, projectsResp, changesSummary] = await Promise.all([
          fetchAdminUsers(),
          fetchAdminProjects({ offset: 0, limit: 10000, fromDate: range.from, toDate: range.to }),
          fetchAdminChangesSummary().catch(() => ({ items: [] } as AdminClientChangesSummaryListOut)),
        ]);
        const rowsBase = buildClientRows(users, projectsResp.items);
        const pendingMap: Record<number, number> = {};
        changesSummary.items.forEach((i) => {
          pendingMap[i.user.id] = i.pendingChanges;
        });
        const rows = rowsBase.map((r) => ({
          ...r,
          pendingChanges: pendingMap[r.id] ?? 0,
        }));
        setBaseClients(rows);
        setPage(1);
      } catch (e: any) {
        console.error(e);
        setError(e?.message || 'Не удалось загрузить клиентов');
      } finally {
        setLoading(false);
      }
    })();
  }, [range]);

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

  function updateClientById(id: number, updater: (row: ClientRow) => ClientRow) {
    setBaseClients((prev) => prev.map((c) => (c.id === id ? updater(c) : c)));
  }

  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const pageSafe = Math.min(Math.max(page, 1), totalPages);
  const start = (pageSafe - 1) * pageSize;
  const end = start + pageSize;
  const pageRows = filtered.slice(start, end);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div className="table-card">
        <div className="table-toolbar">
          <div
            className="filters"
            style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', width: '100%' }}
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
              style={{ minWidth: 240, marginLeft: 'auto' }}
            />
          </div>
          <div className="actions">
            {loading ? (
              <span className="sub">Загрузка…</span>
            ) : (
              <span className="sub">
                Всего клиентов: {clients.length}. Период: {range.from} — {range.to}
              </span>
            )}
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
                pageRows.map((row, idx) => (
                  <tr
                    key={row.id}
                    className={idx % 2 === 0 ? 'row-alt' : ''}
                    style={{ cursor: 'pointer' }}
                    onClick={() => {
                      setSelectedClientId(row.id);
                    }}
                  >
                    <td className="muted">{row.id}</td>
                    <td>
                      <div className="name">{row.name}</div>
                      {row.pendingChanges > 0 && (
                        <div className="sub" style={{ marginTop: 2 }}>
                          <span
                            className="badge badge--orange"
                            style={{ fontWeight: 500 }}
                          >
                            Изменения: {row.pendingChanges}
                          </span>
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
            Итого за период: клиентов {filtered.length}, проектов {totals.totalProjects}
          </span>
          <span>
            Суммарный остаток: {totals.totalRemaining}, общий объём данных: {totals.totalVolume}
          </span>
          <span style={{ opacity: 0.7 }}>
            Демонстрационный расчёт на фронтенде, без точных данных биллинга.
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
              {selectedClient.pendingChanges > 0 && (
                <div className="sub" style={{ marginTop: 4 }}>
                  Необработанных изменений: {selectedClient.pendingChanges}
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
                  {selectedClient.pendingChanges > 0
                    ? ` (${selectedClient.pendingChanges})`
                    : ''}
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
                className="btn btn--ghost"
                onClick={() => {
                  setSelectedClientId(null);
                }}
              >
                ← К списку клиентов
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
                <div>{selectedClient.remaining}</div>
              </div>
              <div>
                <div className="sub">Общий объём данных за период</div>
                <div>{selectedClient.totalVolume}</div>
              </div>
            </div>
            <div
              style={{
                marginTop: 8,
                paddingTop: 8,
                borderTop: '1px solid #eee',
                display: 'grid',
                gap: 8,
              }}
            >
              <div className="sub">
                Управление клиентом (демо, только на фронте — без сохранения на сервер):
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => {
                    if (!selectedClient) return;
                    const input = window.prompt(
                      'На сколько данных увеличить остаток клиента? (демо, только фронтенд)',
                    );
                    if (!input) return;
                    const amount = Number(input.replace(',', '.'));
                    if (!Number.isFinite(amount) || amount <= 0) {
                      alert('Введите положительное число');
                      return;
                    }
                    updateClientById(selectedClient.id, (row) => ({
                      ...row,
                      baseRemaining: row.baseRemaining + amount,
                    }));
                  }}
                >
                  Начислить данные
                </button>
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => {
                    if (!selectedClient) return;
                    const input = window.prompt(
                      'Сколько данных списать с клиента? (демо, только фронтенд)',
                    );
                    if (!input) return;
                    const amount = Number(input.replace(',', '.'));
                    if (!Number.isFinite(amount) || amount <= 0) {
                      alert('Введите положительное число');
                      return;
                    }
                    updateClientById(selectedClient.id, (row) => ({
                      ...row,
                      baseRemaining: Math.max(0, row.baseRemaining - amount),
                    }));
                  }}
                >
                  Списать данные
                </button>
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => {
                    if (!selectedClient) return;
                    const limitInput = window.prompt(
                      'Установить новый тариф (общий лимит данных клиента)? (демо, только фронтенд)',
                    );
                    if (!limitInput) return;
                    const newLimit = Number(limitInput.replace(',', '.'));
                    if (!Number.isFinite(newLimit) || newLimit <= 0) {
                      alert('Введите положительное число');
                      return;
                    }
                    updateClientById(selectedClient.id, (row) => {
                      const baseUsed = row.baseTotalVolume;
                      const remaining = Math.max(0, newLimit - baseUsed);
                      return {
                        ...row,
                        baseRemaining: remaining,
                      };
                    });
                  }}
                >
                  Изменить тариф (лимит)
                </button>
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => {
                    if (!selectedClient) return;
                    const next =
                      selectedClient.status === 'На паузе' ? 'Активен' : 'На паузе';
                    updateClientById(selectedClient.id, (row) => ({
                      ...row,
                      status: next,
                    }));
                  }}
                >
                  {selectedClient.status === 'На паузе'
                    ? 'Снять с паузы (демо)'
                    : 'Поставить на паузу (демо)'}
                </button>
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => {
                    if (!selectedClient) return;
                    const confirmOff = window.confirm(
                      'Отключить клиента? (демо, только фронтенд — без изменения реальных проектов)',
                    );
                    if (!confirmOff) return;
                    updateClientById(selectedClient.id, (row) => ({
                      ...row,
                      status: 'Отключен',
                    }));
                  }}
                >
                  Отключить клиента (демо)
                </button>
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
  );
}

export default AdminClientsScreen;


