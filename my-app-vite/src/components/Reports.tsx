import { useEffect, useState } from 'react';
import { fetchReports, type ReportItem, buildLeadsExportUrl } from '../api';

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

  async function load(p = page, s = pageSize) {
    try {
      setLoading(true);
      setError(null);
      const offset = (p - 1) * s;
      const resp = await fetchReports({ offset, limit: s });
      setItems(resp.items);
      setTotal(resp.total);
    } catch (e: any) {
      setError(e?.message || 'Не удалось загрузить отчёты');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(1);
  }, []);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters" />
        <div className="actions">
          {loading ? <span className="sub">Загрузка…</span> : <span className="sub">Всего отчётов: {total}</span>}
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
                  Отчётов пока нет. Сделайте экспорт в разделе «Идентификации».
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
            {items.map((r, idx) => {
              const projectIds = parseProjectIds(r.projectIds);
              return (
                <tr key={r.id} className={idx % 2 === 0 ? 'row-alt' : ''}>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}>{r.createdAt}</td>
                  <td className="muted">{r.fromDate} — {r.toDate}</td>
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
                        const url = buildLeadsExportUrl({
                          projectIds,
                          fromDate: r.fromDate,
                          toDate: r.toDate,
                          format: (r.format || 'csv') as 'csv' | 'xlsx',
                          source: 'reports',
                        });
                        window.open(url, '_blank');
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
    </div>
  );
}

export default Reports;


