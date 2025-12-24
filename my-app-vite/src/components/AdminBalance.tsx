// Раздел «Баланс» для админа: операции по номерам (идентификациям) на уровне клиента
import { useEffect, useState } from 'react';
import DateRangeFilter from './DateRangeFilter';
import {
  fetchAdminUsers,
  fetchAdminClientBalanceSummary,
  fetchAdminClientBalanceOps,
  createAdminClientBalanceOp,
  type UserInfo,
  type BalanceOperation,
  type ClientBalanceSummary,
} from '../api';

type AdminBalanceProps = {
  initialClientId?: number | null;
  initialModalType?: 'credit' | 'debit' | null;
};

type DateRange = { from: string; to: string };

function getTodayRange(): DateRange {
  const today = new Date().toISOString().slice(0, 10);
  return { from: today, to: today };
}

type OperationModalProps = {
  clientId: number;
  type: 'credit' | 'debit';
  onClose: () => void;
  onDone: () => void;
};

function OperationModal({ clientId, type, onClose, onDone }: OperationModalProps) {
  const [amount, setAmount] = useState<number>(0);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (amount <= 0) {
      setError('Укажите положительное число');
      return;
    }
    try {
      setSubmitting(true);
      setError(null);
      await createAdminClientBalanceOp(clientId, { amount, type, comment: comment.trim() || undefined });
      onDone();
    } catch (err: any) {
      setError(err?.message || 'Не удалось сохранить операцию');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div
        className="modal"
        style={{
          maxWidth: 460,
          borderRadius: 12,
          padding: 0,
          overflow: 'hidden',
        }}
      >
        <div className="modal__header" style={{ padding: '14px 16px', borderBottom: '1px solid #eee' }}>
          <div style={{ fontWeight: 600 }}>
            {type === 'credit' ? 'Начислить номера' : 'Списать номера'}
          </div>
        </div>
        <form onSubmit={handleSubmit} className="modal__body" style={{ display: 'grid', gap: 12, padding: '16px 16px 12px' }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="sub">Количество номеров</span>
            <input
              type="number"
              min={1}
              value={amount}
              onChange={(e) => setAmount(Number(e.target.value))}
              required
              style={{
                border: '1px solid #dfe3eb',
                borderRadius: 8,
                padding: '10px 12px',
                width: '100%',
                outline: 'none',
              }}
            />
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="sub">Комментарий (необязательно)</span>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
              style={{
                border: '1px solid #dfe3eb',
                borderRadius: 8,
                padding: '10px 12px',
                width: '100%',
                outline: 'none',
                resize: 'vertical',
                minHeight: 96,
              }}
            />
          </label>
          {error && <div className="sub" style={{ color: '#d00' }}>{error}</div>}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, paddingTop: 4 }}>
            <button type="button" className="btn btn--ghost" onClick={onClose}>Отмена</button>
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? 'Сохранение…' : 'Сохранить'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function AdminBalance({ initialClientId = null, initialModalType = null }: AdminBalanceProps) {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [selectedClientId, setSelectedClientId] = useState<number | null>(initialClientId ?? null);
  const [range, setRange] = useState<DateRange>(() => getTodayRange());
  const [summary, setSummary] = useState<ClientBalanceSummary | null>(null);
  const [ops, setOps] = useState<BalanceOperation[]>([]);
  const [totalOps, setTotalOps] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingOps, setLoadingOps] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<null | { type: 'credit' | 'debit' }>(initialModalType ? { type: initialModalType } : null);

  useEffect(() => {
    (async () => {
      try {
        const list = await fetchAdminUsers();
        setUsers(list);
      } catch (e) {
        console.error(e);
      }
    })();
  }, []);

  // Применяем стартовое значение клиента при смене пропсов
  useEffect(() => {
    if (initialClientId != null) {
      setSelectedClientId(initialClientId);
    }
    if (initialModalType) {
      setModal({ type: initialModalType });
    }
  }, [initialClientId, initialModalType]);

  const hasClient = selectedClientId != null;

  async function loadSummary(clientId: number, r: DateRange = range) {
    try {
      setLoadingSummary(true);
      const data = await fetchAdminClientBalanceSummary(clientId, { fromDate: r.from, toDate: r.to });
      setSummary(data);
    } catch (e: any) {
      setError(e?.message || 'Не удалось загрузить баланс');
    } finally {
      setLoadingSummary(false);
    }
  }

  async function loadOps(clientId: number, p = page, s = pageSize, r: DateRange = range) {
    try {
      setLoadingOps(true);
      const offset = (p - 1) * s;
      const resp = await fetchAdminClientBalanceOps(clientId, { fromDate: r.from, toDate: r.to, offset, limit: s });
      setOps(resp.items);
      setTotalOps(resp.total);
    } catch (e: any) {
      setError(e?.message || 'Не удалось загрузить операции');
    } finally {
      setLoadingOps(false);
    }
  }

  useEffect(() => {
    if (!hasClient || selectedClientId == null) return;
    loadSummary(selectedClientId);
    loadOps(selectedClientId, 1, pageSize);
    setPage(1);
  }, [selectedClientId, range]);

  const totalPages = Math.max(1, Math.ceil(totalOps / pageSize));

  return (
    <div className="table-card" style={{ display: 'grid', gap: 16 }}>
      <div className="table-toolbar" style={{ gap: 12 }}>
        <div className="filters" style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <select
            value={selectedClientId ?? ''}
            onChange={(e) => {
              const val = e.target.value ? Number(e.target.value) : null;
              setSelectedClientId(val);
            }}
          >
            <option value="">Выберите клиента</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.login} (id: {u.id})
              </option>
            ))}
          </select>
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
          {!hasClient && <span className="sub">Выберите клиента, чтобы увидеть баланс</span>}
          {loadingSummary && <span className="sub">Загрузка…</span>}
        </div>
      </div>

      {error && (
        <div className="sub" style={{ color: '#d00' }}>
          {error}
        </div>
      )}

      {hasClient && summary && (
        <div style={{ overflowX: 'auto' }}>
          <div
            className="table-card"
            style={{ display: 'grid', gap: 12, minWidth: 720, padding: '12px 16px' }}
          >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontWeight: 600 }}>Сводка по клиенту</div>
              <div className="sub">
                Клиент id: {summary.clientId}. Период: {summary.periodFrom} — {summary.periodTo}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="btn btn--primary" type="button" onClick={() => setModal({ type: 'credit' })}>
                Начислить номера
              </button>
              <button className="btn btn--secondary" type="button" onClick={() => setModal({ type: 'debit' })}>
                Списать номера
              </button>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <div className="sub">Текущий остаток</div>
              <div style={{ fontSize: 20, fontWeight: 600, color: summary.debt ? '#d23' : '#111' }}>
                {summary.remaining}
              </div>
              {summary.debt && <div className="sub" style={{ color: '#d23' }}>Долг</div>}
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

      {hasClient && (
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
                    if (selectedClientId != null) loadOps(selectedClientId, p);
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
                    if (selectedClientId != null) loadOps(selectedClientId, p);
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
                    if (selectedClientId != null) loadOps(selectedClientId, 1, s);
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
      )}

      {modal && selectedClientId != null && (
        <OperationModal
          clientId={selectedClientId}
          type={modal.type}
          onClose={() => setModal(null)}
          onDone={() => {
            setModal(null);
            loadSummary(selectedClientId);
            loadOps(selectedClientId, 1, pageSize);
            setPage(1);
          }}
        />
      )}
    </div>
  );
}

export default AdminBalance;


