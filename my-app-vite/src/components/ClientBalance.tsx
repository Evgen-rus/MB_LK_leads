// Раздел «Баланс» для клиента: только просмотр сводки и истории операций по своему аккаунту
import { useEffect, useState, useCallback } from 'react';
import DateRangeFilter from './DateRangeFilter';
import {
  fetchClientBalanceSummary,
  fetchClientBalanceOps,
  type BalanceOperation,
  type ClientBalanceSummary,
} from '../api';

type DateRange = { from: string; to: string };

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

function ClientBalance() {
  const [range, setRange] = useState<DateRange>(() => getTodayRange());
  const [summary, setSummary] = useState<ClientBalanceSummary | null>(null);
  const [ops, setOps] = useState<BalanceOperation[]>([]);
  const [totalOps, setTotalOps] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingOps, setLoadingOps] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSummary = useCallback(async (r: DateRange = range) => {
    try {
      setLoadingSummary(true);
      setError(null);
      const data = await fetchClientBalanceSummary({ fromDate: r.from, toDate: r.to });
      setSummary(data);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить баланс'));
    } finally {
      setLoadingSummary(false);
    }
  }, [range]);

  // Не привязываем к state page/pageSize, чтобы пагинация не сбрасывала загрузку на первую
  const loadOps = useCallback(async (p: number, s = pageSize, r: DateRange = range) => {
    try {
      setLoadingOps(true);
      setError(null);
      const offset = (p - 1) * s;
      const resp = await fetchClientBalanceOps({ fromDate: r.from, toDate: r.to, offset, limit: s });
      setOps(resp.items);
      setTotalOps(resp.total);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить операции'));
    } finally {
      setLoadingOps(false);
    }
  }, [pageSize, range]);

  useEffect(() => {
    loadSummary(range);
    loadOps(1, pageSize, range);
    setPage(1);
  }, [range, loadSummary, loadOps, pageSize]);

  const totalPages = Math.max(1, Math.ceil(totalOps / pageSize));

  return (
    <div className="table-card" style={{ display: 'grid', gap: 16 }}>
      <div className="table-toolbar" style={{ gap: 12 }}>
        <div className="filters" style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <DateRangeFilter
            from={range.from}
            to={range.to}
            onChange={(r) => {
              setRange(r);
              setPage(1);
            }}
          />
        </div>
        <div className="actions">
          {loadingSummary && <span className="sub">Загрузка…</span>}
        </div>
      </div>

      {error && (
        <div className="sub" style={{ color: '#d00' }}>
          {error}
        </div>
      )}

      {summary && (
        <div style={{ overflowX: 'auto' }}>
          <div
            className="table-card"
            style={{ display: 'grid', gap: 12, minWidth: 720, padding: '12px 16px' }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontWeight: 600 }}>Сводка по вашему аккаунту</div>
                <div className="sub">
                  Период: {summary.periodFrom} — {summary.periodTo}
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              <div>
                <div className="sub">Текущий остаток</div>
                <div style={{ fontSize: 20, fontWeight: 600, color: summary.debt ? '#d23' : '#111' }}>
                  {summary.remaining}
                </div>
                {summary.debt && <div className="sub" style={{ color: '#d23' }}>Долг</div>}
                {summary.debt && (
                  <div className="sub" style={{ color: '#666', maxWidth: 520 }}>
                    Отрицательный остаток означает долг. Чтобы продолжить получать номера, нужно пополнить баланс.
                  </div>
                )}
              </div>
              <div>
                <div className="sub">Начислено всего</div>
                <div>{summary.credited}</div>
              </div>
              <div>
                <div className="sub">Списано вручную</div>
                <div>{summary.debited}</div>
              </div>
              <div>
                <div className="sub">Выдано номеров (всего)</div>
                <div>{summary.usedTotal}</div>
              </div>
              <div>
                <div className="sub">Выдано за период</div>
                <div>{summary.usedPeriod}</div>
              </div>
            </div>
          </div>
        </div>
      )}

      <div style={{ overflowX: 'auto' }}>
        <div className="table-card" style={{ minWidth: 860, padding: '0 8px 8px' }}>
          <div className="table-toolbar">
            <div className="filters">
              <span className="sub">Операции</span>
            </div>
            <div className="actions">
              {loadingOps ? <span className="sub">Загрузка…</span> : <span className="sub">Всего: {totalOps}</span>}
            </div>
          </div>
          <div className="table-footer table-footer--top">
            Показано {ops.length} из {totalOps}
            <div className="spacer" />
            <div className="pager">
              <button
                className="pager__btn"
                disabled={page <= 1}
                onClick={() => {
                  const p = Math.max(1, page - 1);
                  setPage(p);
                  loadOps(p);
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
                  loadOps(p);
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
                  loadOps(1, s);
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
                  <th>Дата</th>
                  <th>Тип</th>
                  <th>Количество</th>
                  <th>Комментарий</th>
                  <th>Создал</th>
                </tr>
              </thead>
              <tbody>
                {!loadingOps && ops.length === 0 && (
                  <tr>
                    <td colSpan={5} className="muted" style={{ padding: 16 }}>Операций нет.</td>
                  </tr>
                )}
                {ops.map((op) => (
                  <tr key={op.id}>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{op.createdAt}</td>
                    <td>
                      <span
                        className={op.type === 'credit' ? 'badge badge--green' : 'badge badge--orange'}
                        style={{ textTransform: 'capitalize' }}
                      >
                        {op.type === 'credit' ? 'Начисление' : 'Списание'}
                      </span>
                    </td>
                    <td>{op.amount}</td>
                    <td>{op.comment || '—'}</td>
                    <td className="muted">{op.createdBy.login} (id: {op.createdBy.id})</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-footer">
            Показано {ops.length} из {totalOps}
            <div className="spacer" />
            <div className="pager">
              <button
                className="pager__btn"
                disabled={page <= 1}
                onClick={() => {
                  const p = Math.max(1, page - 1);
                  setPage(p);
                  loadOps(p);
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
                  loadOps(p);
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
                  loadOps(1, s);
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
      </div>
    </div>
  );
}

export default ClientBalance;


