// Отчёты всех клиентов (для админа)
// Включает столбец "Клиент" с логином и id
import { useEffect, useState, useCallback } from 'react';
import {
  fetchAdminReports,
  fetchAdminUsers,
  fetchAdminProjects,
  createAdminReport,
  downloadLeadsExport,
  type AdminReportItem,
  type UserInfo,
} from '../api';
import ReportBuildModal from './ReportBuildModal';
import DateTimeCompact from './DateTimeCompact';
import DateRangeCompact from './DateRangeCompact';

type ClientSelection = number | 'all' | null;
type ManagerRole = 'admin' | 'agent';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function AdminReports({ managerRole }: { managerRole: ManagerRole }) {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [items, setItems] = useState<AdminReportItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [total, setTotal] = useState(0);
  const [buildModalOpen, setBuildModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [selectedClientId, setSelectedClientId] = useState<ClientSelection>(null);
  const [projects, setProjects] = useState<Array<{ id: number; name: string }>>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  async function loadUsers() {
    try {
      const list = await fetchAdminUsers();
      setUsers(list);
    } catch (err: unknown) {
      console.error(err);
      setError(getErrorMessage(err, 'Не удалось загрузить список клиентов'));
    }
  }

  // Не завязываем на state page/pageSize, чтобы смена страницы не сбрасывала данные
  const load = useCallback(
    async (p: number, s = pageSize) => {
      try {
        setLoading(true);
        setError(null);
        const offset = (p - 1) * s;
        const resp = await fetchAdminReports({
          offset,
          limit: s,
        });
        setItems(resp.items);
        setTotal(resp.total);
      } catch (err: unknown) {
        setError(getErrorMessage(err, 'Не удалось загрузить отчёты'));
      } finally {
        setLoading(false);
      }
    },
    [pageSize],
  );

  useEffect(() => {
    loadUsers();
    load(1, pageSize);
  }, [load, pageSize]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  function parseProjectIdsString(raw?: string | null): number[] | undefined {
    const source = (raw ?? '').trim();
    if (!source) return undefined;
    const parts = source.split(',').map((p) => Number(p.trim())).filter((n) => Number.isFinite(n));
    return parts.length ? parts : undefined;
  }

  async function loadProjectsForClient(clientId: ClientSelection) {
    if (typeof clientId !== 'number') {
      setProjects([]);
      return;
    }
    try {
      setProjectsLoading(true);
      const resp = await fetchAdminProjects({ offset: 0, limit: 10000, userId: clientId });
      setProjects(resp.items.filter((p) => p.status !== 'Удалён').map((p) => ({ id: p.id, name: p.name })));
    } catch (err) {
      console.error(err);
      setProjects([]);
    } finally {
      setProjectsLoading(false);
    }
  }

  async function handleCreateReport(payload: {
    fromDate: string;
    toDate: string;
    format: 'csv' | 'xlsx';
    projectIds?: number[];
    clientId?: number | null;
  }) {
    try {
      setCreating(true);
      await createAdminReport({
        fromDate: payload.fromDate,
        toDate: payload.toDate,
        projectIds: payload.clientId == null ? undefined : payload.projectIds,
        format: payload.format,
        clientId: payload.clientId,
      });
      setBuildModalOpen(false);
      setPage(1);
      await load(1, pageSize);
      window.dispatchEvent(new CustomEvent('app-toast', { detail: 'Отчёт сформирован. Теперь его можно скачать в списке ниже.' }));
    } catch (err: unknown) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось создать отчёт') }));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => {
              setBuildModalOpen(true);
              void loadProjectsForClient(selectedClientId);
            }}
          >
            Сформировать отчёт
          </button>
        </div>
        <div className="actions" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {loading ? <span className="sub">Загрузка…</span> : <span className="sub">Всего отчётов: {total}</span>}
        </div>
      </div>
      <div className="sub" style={{ padding: '0 16px 8px' }}>
        Отчёты формируются через кнопку «Сформировать отчёт» и доступны для скачивания в списке ниже.
      </div>
      <div className="table-footer table-footer--top">
        Показано {items.length} из {total}
        <div className="spacer" />
        <div className="pager">
          <button
            className="pager__btn"
            disabled={page <= 1}
            onClick={() => {
              const p = Math.max(1, page - 1);
              setPage(p);
              load(p);
            }}
          >
            ‹
          </button>
          <span className="pager__info">
            {page} / {totalPages}
          </span>
          <button
            className="pager__btn"
            disabled={page >= totalPages}
            onClick={() => {
              const p = Math.min(totalPages, page + 1);
              setPage(p);
              load(p);
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
              load(1, s);
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
              <th>Сформировал</th>
              <th>Клиент</th>
              <th>Дата создания</th>
              <th>Период</th>
              <th>Формат</th>
              <th>Проекты</th>
              <th>Скачать</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && !loading && !error && (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', padding: 16, color: '#666' }}>
                  Отчётов пока нет. Нажмите «Сформировать отчёт».
                </td>
              </tr>
            )}
            {error && (
              <tr>
                <td colSpan={7} style={{ color: '#d00', padding: 16 }}>
                  {error}
                </td>
              </tr>
            )}
            {items.map((r) => {
              const projectIds = parseProjectIdsString(r.projectIds);
              return (
                <tr key={r.id}>
                  <td>
                    <div className="name">{r.user.name || r.user.login}</div>
                    <div className="sub">id: {r.user.id}</div>
                  </td>
                  <td>
                    <div className="name">{r.client ? (r.client.name || r.client.login) : 'Все клиенты'}</div>
                    {r.client && <div className="sub">id: {r.client.id}</div>}
                  </td>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}><DateTimeCompact value={r.createdAt} /></td>
                  <td className="muted"><DateRangeCompact from={r.fromDate} to={r.toDate} /></td>
                  <td className="muted" style={{ textTransform: 'uppercase' }}>{r.format}</td>
                  <td className="muted">
                    {projectIds && projectIds.length
                      ? `${projectIds.length} шт.`
                      : 'Все проекты'}
                  </td>
                  <td>
                    <button
                      className="btn btn--secondary"
                      onClick={() => {
                        void downloadLeadsExport({
                          projectIds,
                          fromDate: r.fromDate,
                          toDate: r.toDate,
                          format: (r.format || 'csv') as 'csv' | 'xlsx',
                          source: 'reports',
                          clientId: r.client?.id ?? undefined,
                        });
                      }}
                    >
                      Скачать
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="table-footer">
        Показано {items.length} из {total}
        <div className="spacer" />
        <div className="pager">
          <button
            className="pager__btn"
            disabled={page <= 1}
            onClick={() => {
              const p = Math.max(1, page - 1);
              setPage(p);
              load(p);
            }}
          >
            ‹
          </button>
          <span className="pager__info">
            {page} / {totalPages}
          </span>
          <button
            className="pager__btn"
            disabled={page >= totalPages}
            onClick={() => {
              const p = Math.min(totalPages, page + 1);
              setPage(p);
              load(p);
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
              load(1, s);
            }}
          >
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>
      {buildModalOpen && (
        <ReportBuildModal
          title="Сформировать отчёт"
          onClose={() => {
            if (!creating) setBuildModalOpen(false);
          }}
          onSubmit={handleCreateReport}
          submitting={creating}
          users={users.map((u) => ({ id: u.id, name: u.name || u.login, workStatus: u.workStatus }))}
          selectedClientId={selectedClientId}
          allowAllClients={managerRole === 'admin'}
          onClientChange={(clientId) => {
            setSelectedClientId(clientId);
            void loadProjectsForClient(clientId);
          }}
          projects={projects}
          projectsLoading={projectsLoading}
        />
      )}
    </div>
  );
}

export default AdminReports;

