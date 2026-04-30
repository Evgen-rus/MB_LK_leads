import { useCallback, useEffect, useMemo, useState } from 'react';
import DateRangeFilter from './DateRangeFilter';
import DateTimeCompact from './DateTimeCompact';
import DateRangeCompact from './DateRangeCompact';
import HistoryEventCardButton from './HistoryEventCardButton';
import { fetchClientActivityEvents, type ActivityEntity, type ActivityEvent } from '../api';
import { formatProjectNameForDisplay, formatSourceTextForDisplay } from '../utils/sourceCodeDisplay';

type DateRange = { from: string; to: string };

const PAGE_SIZE = 50;

function formatDateInput(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function getDefaultRange(): DateRange {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - 6);
  return { from: formatDateInput(from), to: formatDateInput(to) };
}

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function ClientActivityHistory() {
  const [range, setRange] = useState<DateRange>(() => getDefaultRange());
  const [entityFilter, setEntityFilter] = useState<'all' | ActivityEntity>('all');
  const [search, setSearch] = useState('');
  const [rows, setRows] = useState<ActivityEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (nextPage = page) => {
    try {
      setLoading(true);
      setError(null);
      const offset = (nextPage - 1) * PAGE_SIZE;
      const resp = await fetchClientActivityEvents({
        offset,
        limit: PAGE_SIZE,
        fromDate: range.from,
        toDate: range.to,
        entities: entityFilter === 'all' ? undefined : [entityFilter],
        q: search.trim() || undefined,
      });
      setRows(resp.items);
      setTotal(resp.total);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить историю изменений'));
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [entityFilter, page, range.from, range.to, search]);

  useEffect(() => {
    load(page);
  }, [page, load]);

  useEffect(() => {
    setPage(1);
  }, [range, entityFilter, search]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const entityLabel = useMemo(
    () =>
      (entity: ActivityEvent['entity']) =>
        entity === 'project'
          ? 'Проект'
          : entity === 'balance'
          ? 'Баланс'
          : entity === 'report'
          ? 'Отчёт'
          : 'ЧС',
    [],
  );

  return (
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
          <select
            value={entityFilter}
            onChange={(e) => {
              const next = e.target.value as 'all' | ActivityEntity;
              setEntityFilter(next);
              setPage(1);
            }}
          >
            <option value="all">Все события</option>
            <option value="project">Проекты</option>
            <option value="balance">Баланс</option>
            <option value="report">Отчёты</option>
            <option value="blacklist">Чёрный список</option>
          </select>
          <input
            type="search"
            placeholder="Поиск по ID, кто, что сделал"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            style={{ minWidth: 240, flex: 1 }}
          />
        </div>
        <div className="actions toolbar-right">
          {loading ? <span className="toolbar-meta">Загрузка…</span> : <span className="toolbar-meta">Всего: {total}</span>}
        </div>
      </div>

      <div className="table-footer table-footer--top">
        Показано {rows.length} из {total}
        <div className="spacer" />
        <div className="pager">
          <button
            className="pager__btn"
            disabled={page <= 1}
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
          >
            ‹
          </button>
          <span className="pager__info">
            {page} / {totalPages}
          </span>
          <button
            className="pager__btn"
            disabled={page >= totalPages}
            onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
          >
            ›
          </button>
        </div>
      </div>

      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>ID события</th>
              <th>Дата/время</th>
              <th>Кто</th>
              <th>Что сделано</th>
              <th>Результат</th>
              <th>Сущность</th>
              <th>Карточка</th>
            </tr>
          </thead>
          <tbody>
            {!loading && !error && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="muted" style={{ padding: 16 }}>
                  Событий по выбранным фильтрам не найдено.
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
            {rows.map((row) => (
              <tr key={row.eventId}>
                <td className="muted" style={{ whiteSpace: 'nowrap' }}>{row.eventId}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <DateTimeCompact value={row.createdAt} />
                </td>
                <td>
                  {row.actor ? (
                    <>
                      <div className="name">{row.actor.name || row.actor.login}</div>
                      <div className="sub">id: {row.actor.id}</div>
                    </>
                  ) : (
                    <span className="muted">Система</span>
                  )}
                </td>
                <td>
                  <div>{formatSourceTextForDisplay(row.description)}</div>
                  {row.entity === 'report' && row.periodFrom && row.periodTo && (
                    <div className="sub">
                      Период: <DateRangeCompact from={row.periodFrom} to={row.periodTo} />
                    </div>
                  )}
                  {row.projectName && <div className="sub">{formatProjectNameForDisplay(row.projectName)}</div>}
                </td>
                <td>
                  <span className={row.outcome === 'failed' ? 'badge badge--orange' : 'badge badge--green'} style={{ fontWeight: 500 }}>
                    {row.outcome === 'failed' ? 'Ошибка' : 'Успешно'}
                  </span>
                </td>
                <td>
                  <span className="badge badge--gray" style={{ fontWeight: 500 }}>
                    {entityLabel(row.entity)}
                  </span>
                </td>
                <td>
                  {row.entity === 'project' ? (
                    <HistoryEventCardButton
                      eventId={row.eventId}
                      className="btn"
                      style={{ padding: '4px 8px' }}
                    />
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="table-footer">
        Показано {rows.length} из {total}
        <div className="spacer" />
        <div className="pager">
          <button
            className="pager__btn"
            disabled={page <= 1}
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
          >
            ‹
          </button>
          <span className="pager__info">
            {page} / {totalPages}
          </span>
          <button
            className="pager__btn"
            disabled={page >= totalPages}
            onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
          >
            ›
          </button>
        </div>
      </div>
    </div>
  );
}

export default ClientActivityHistory;
