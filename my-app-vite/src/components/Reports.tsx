import { useEffect, useState, useCallback } from 'react';
import { fetchReports, createReport, fetchProjects, type ReportItem, downloadLeadsExport } from '../api';
import type { Project } from '../types/project';
import ReportBuildModal from './ReportBuildModal';
import DateTimeCompact from './DateTimeCompact';
import DateRangeCompact from './DateRangeCompact';

function parseProjectIds(projectIds?: string | null): number[] | undefined {
  if (!projectIds) return undefined;
  const parts = projectIds.split(',').map(p => p.trim()).filter(Boolean);
  if (!parts.length) return undefined;
  const nums = parts.map(p => Number(p)).filter(n => Number.isFinite(n));
  return nums.length ? nums : undefined;
}

function Reports() {
  const [items, setItems] = useState<ReportItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [total, setTotal] = useState(0);
  const [buildModalOpen, setBuildModalOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  // Отвязываем от state page/pageSize, чтобы клики по пагинации не обнуляли данные
  const load = useCallback(
    async (p: number, s = pageSize) => {
      try {
        setLoading(true);
        setError(null);
        const offset = (p - 1) * s;
        const resp = await fetchReports({
          offset,
          limit: s,
        });
        setItems(resp.items);
        setTotal(resp.total);
      } catch (err: unknown) {
        const msg = err && typeof err === 'object' && 'message' in err && typeof (err as { message?: unknown }).message === 'string'
          ? (err as { message: string }).message
          : 'Не удалось загрузить отчёты';
        setError(msg);
      } finally {
        setLoading(false);
      }
    },
    [pageSize],
  );

  useEffect(() => {
    load(1, pageSize);
  }, [load, pageSize]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  async function loadProjectsForModal() {
    if (projects.length > 0) return;
    try {
      setProjectsLoading(true);
      const resp = await fetchProjects({ offset: 0, limit: 10000 });
      setProjects(resp.items.filter((p) => p.status !== 'Удалён'));
    } catch (err) {
      console.error(err);
    } finally {
      setProjectsLoading(false);
    }
  }

  async function handleCreateReport(payload: {
    fromDate: string;
    toDate: string;
    format: 'csv' | 'xlsx';
    projectIds?: number[];
  }) {
    try {
      setCreating(true);
      await createReport({
        fromDate: payload.fromDate,
        toDate: payload.toDate,
        projectIds: payload.projectIds,
        format: payload.format,
      });
      setBuildModalOpen(false);
      setPage(1);
      await load(1, pageSize);
      window.dispatchEvent(new CustomEvent('app-toast', { detail: 'Отчёт сформирован. Теперь его можно скачать в списке ниже.' }));
    } catch (err) {
      console.error(err);
      window.dispatchEvent(new CustomEvent('app-toast', { detail: 'Не удалось сформировать отчёт. Попробуйте позже.' }));
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
              void loadProjectsForModal();
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
                <td colSpan={5} style={{ textAlign: 'center', padding: 16, color: '#666' }}>
                  Отчётов пока нет. Нажмите «Сформировать отчёт».
                </td>
              </tr>
            )}
            {error && (
              <tr>
                <td colSpan={5} style={{ color: '#d00', padding: 16 }}>
                  {error}
                </td>
              </tr>
            )}
            {items.map((r) => {
              const projectIds = parseProjectIds(r.projectIds);
              return (
                <tr key={r.id}>
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
          projects={projects.map((p) => ({ id: p.id, name: p.name }))}
          projectsLoading={projectsLoading}
        />
      )}
    </div>
  );
}

export default Reports;


