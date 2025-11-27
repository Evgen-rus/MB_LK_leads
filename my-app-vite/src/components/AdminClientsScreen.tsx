// Экран «Клиенты» для админа: список клиентов с агрегированной статистикой по проектам
// Логика максимально простая и прозрачная, без лишних сущностей.
import { useEffect, useMemo, useState } from 'react';
import {
  fetchAdminUsers,
  fetchAdminProjects,
  type UserInfo,
  type AdminProject,
} from '../api';
import AdminClientProjects from './AdminClientProjects';

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
  remaining: number;
  totalVolume: number;
};

const STATUS_COLORS: Record<ClientStatus, string> = {
  Активен: '#4CAF50',
  'На паузе': '#FFC107',
  Отключен: '#9E9E9E',
  'Нет проектов': '#03A9F4',
  Долг: '#F44336',
  Дожим: '#7E57C2',
};

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

function deriveClientStatus(projects: AdminProject[]): ClientStatus {
  if (!projects.length) return 'Нет проектов';
  const anyActive = projects.some((p) => p.status === 'Активен');
  const allPaused = projects.every((p) => p.status === 'На паузе');
  const allDeliveryOff = projects.length > 0 && projects.every((p) => p.deliveryStatus === 'Отключена');

  if (anyActive) return 'Активен';
  if (allPaused) return 'На паузе';
  if (allDeliveryOff) return 'Отключен';

  // Статусы «Долг» и «Дожим» сейчас не считаем автоматически — нет явных данных в БД.
  // При появлении полей биллинга сюда можно добавить нужные правила.
  return 'Активен';
}

function buildClientRows(users: UserInfo[], projects: AdminProject[]): ClientRow[] {
  const byUser = groupProjectsByUser(projects);
  return users.map((u) => {
    const list = byUser.get(u.id) ?? [];
    const projectCount = list.length;
    const totalLimit = list.reduce((sum, p) => sum + (p.dataLimit || 0), 0);
    const totalUsed = list.reduce((sum, p) => sum + (p.numbersTotal || 0), 0);
    const remaining = Math.max(0, totalLimit - totalUsed);
    const status = deriveClientStatus(list);
    return {
      id: u.id,
      name: u.login,
      projectCount,
      status,
      remaining,
      totalVolume: totalUsed,
    };
  });
}

function AdminClientsScreen() {
  const [clients, setClients] = useState<ClientRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [selectedClient, setSelectedClient] = useState<ClientRow | null>(null);
  const [showProjectsForClientId, setShowProjectsForClientId] = useState<number | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const [users, projectsResp] = await Promise.all([
          fetchAdminUsers(),
          fetchAdminProjects({ offset: 0, limit: 10000 }),
        ]);
        const rows = buildClientRows(users, projectsResp.items);
        setClients(rows);
      } catch (e: any) {
        console.error(e);
        setError(e?.message || 'Не удалось загрузить клиентов');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return clients;
    return clients.filter((c) => {
      const nameHit = c.name.toLowerCase().includes(q);
      const idHit = String(c.id).includes(q);
      return nameHit || idHit;
    });
  }, [clients, search]);

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
          <div className="filters">
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
            />
          </div>
          <div className="actions">
            {loading ? (
              <span className="sub">Загрузка…</span>
            ) : (
              <span className="sub">Всего клиентов: {clients.length}</span>
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
                <th>Общий объём данных</th>
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
                      setSelectedClient(row);
                      setShowProjectsForClientId(null);
                    }}
                  >
                    <td className="muted">{row.id}</td>
                    <td>
                      <div className="name">{row.name}</div>
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
                          setSelectedClient(row);
                          setShowProjectsForClientId(null);
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
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                type="button"
                className="btn btn--secondary"
                onClick={() => setShowProjectsForClientId(selectedClient.id)}
              >
                Перейти к проектам клиента
              </button>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => {
                  setSelectedClient(null);
                  setShowProjectsForClientId(null);
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
                <div className="sub">Общий объём данных</div>
                <div>{selectedClient.totalVolume}</div>
              </div>
            </div>
            <div className="sub">
              Фильтры периода («Сегодня», «Неделя» и т.п.) и отдельный список проектов клиента
              можно будет добавить следующим шагом, когда согласуем расчёт метрик.
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

      {selectedClient && showProjectsForClientId === selectedClient.id && (
        <AdminClientProjects clientId={selectedClient.id} clientName={selectedClient.name} />
      )}
    </div>
  );
}

export default AdminClientsScreen;


