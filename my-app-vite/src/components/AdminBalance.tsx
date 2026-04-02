// Раздел «Баланс» для админа: старый баланс + новый тарифный учет
import { useEffect, useMemo, useState, useCallback } from 'react';
import DateTimeCompact from './DateTimeCompact';
import {
  fetchAdminUsers,
  fetchAdminClientBalanceSummary,
  fetchAdminClientBalanceOps,
  createAdminClientBalanceOp,
  fetchAdminClientTariffs,
  fetchAdminTariffOps,
  createAdminClientTariff,
  createAdminTariffOp,
  type UserInfo,
  type BalanceOperation,
  type ClientBalanceSummary,
  type ClientTariff,
  type ClientTariffOperation,
} from '../api';
import { preventNumberInputWheel } from '../utils/numberInput';

type AdminBalanceProps = {
  initialClientId?: number | null;
  initialModalType?: 'credit' | 'debit' | 'tariff' | null;
};

type BalanceModalState = { type: 'credit' | 'debit' } | null;
type TariffModalState =
  | { mode: 'create' }
  | { mode: 'credit' | 'debit'; tariff: ClientTariff }
  | null;

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
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
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось сохранить операцию'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal" style={{ maxWidth: 460, borderRadius: 12, padding: 0, overflow: 'hidden' }}>
        <div className="modal__header" style={{ padding: '14px 16px', borderBottom: '1px solid #eee' }}>
          <div style={{ fontWeight: 600 }}>{type === 'credit' ? 'Начислить номера' : 'Списать номера'}</div>
        </div>
        <form onSubmit={handleSubmit} className="modal__body" style={{ display: 'grid', gap: 12, padding: '16px 16px 12px' }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="sub">Количество номеров</span>
            <input
              type="number"
              min={1}
              value={amount}
              onChange={(e) => setAmount(Number(e.target.value))}
              onWheel={preventNumberInputWheel}
              required
              style={{ border: '1px solid #dfe3eb', borderRadius: 8, padding: '10px 12px', width: '100%', outline: 'none' }}
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

type TariffModalProps = {
  clientId: number;
  state: Exclude<TariffModalState, null>;
  onClose: () => void;
  onDone: (nextTariffId?: number) => void;
};

function TariffModal({ clientId, state, onClose, onDone }: TariffModalProps) {
  const [amount, setAmount] = useState<number>(0);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isCreate = state.mode === 'create';

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (amount <= 0) {
      setError('Укажите положительное число');
      return;
    }
    if (!isCreate && !comment.trim()) {
      setError('Комментарий обязателен');
      return;
    }
    try {
      setSubmitting(true);
      setError(null);
      if (isCreate) {
        const tariff = await createAdminClientTariff(clientId, { amount, comment: comment.trim() || undefined });
        onDone(tariff.id);
        return;
      }
      await createAdminTariffOp(state.tariff.id, {
        amount,
        type: state.mode,
        comment: comment.trim(),
      });
      onDone(state.tariff.id);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось сохранить изменение тарифа'));
    } finally {
      setSubmitting(false);
    }
  }

  const title = isCreate
    ? 'Добавить тариф'
    : state.mode === 'credit'
      ? `Добавить к тарифу #${state.tariff.id}`
      : `Списать из тарифа #${state.tariff.id}`;

  return (
    <div className="modal-backdrop">
      <div className="modal" style={{ maxWidth: 480, borderRadius: 12, padding: 0, overflow: 'hidden' }}>
        <div className="modal__header" style={{ padding: '14px 16px', borderBottom: '1px solid #eee' }}>
          <div style={{ fontWeight: 600 }}>{title}</div>
          {!isCreate && <div className="sub">Текущее значение тарифа: {state.tariff.currentAmount}</div>}
        </div>
        <form onSubmit={handleSubmit} className="modal__body" style={{ display: 'grid', gap: 12, padding: '16px 16px 12px' }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="sub">{isCreate ? 'Размер тарифа' : 'Количество номеров'}</span>
            <input
              type="number"
              min={1}
              value={amount}
              onChange={(e) => setAmount(Number(e.target.value))}
              onWheel={preventNumberInputWheel}
              required
              style={{ border: '1px solid #dfe3eb', borderRadius: 8, padding: '10px 12px', width: '100%', outline: 'none' }}
            />
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="sub">{isCreate ? 'Комментарий (необязательно)' : 'Комментарий (обязательно)'}</span>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
              required={!isCreate}
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
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
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
  const [summary, setSummary] = useState<ClientBalanceSummary | null>(null);
  const [ops, setOps] = useState<BalanceOperation[]>([]);
  const [totalOps, setTotalOps] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingOps, setLoadingOps] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<BalanceModalState>(
    initialModalType === 'credit' || initialModalType === 'debit'
      ? { type: initialModalType }
      : null,
  );

  const [tariffs, setTariffs] = useState<ClientTariff[]>([]);
  const [totalTariffs, setTotalTariffs] = useState(0);
  const [loadingTariffs, setLoadingTariffs] = useState(false);
  const [selectedTariffId, setSelectedTariffId] = useState<number | null>(null);
  const [tariffOps, setTariffOps] = useState<ClientTariffOperation[]>([]);
  const [totalTariffOps, setTotalTariffOps] = useState(0);
  const [tariffOpsPage, setTariffOpsPage] = useState(1);
  const [tariffOpsPageSize, setTariffOpsPageSize] = useState(10);
  const [loadingTariffOps, setLoadingTariffOps] = useState(false);
  const [tariffModal, setTariffModal] = useState<TariffModalState>(null);

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

  useEffect(() => {
    if (initialClientId != null) {
      setSelectedClientId(initialClientId);
    }
    if (initialModalType === 'tariff') {
      setModal(null);
      setTariffModal({ mode: 'create' });
      return;
    }
    if (initialModalType) {
      setTariffModal(null);
      setModal({ type: initialModalType });
    }
  }, [initialClientId, initialModalType]);

  const hasClient = selectedClientId != null;
  const selectedTariff = useMemo(
    () => (selectedTariffId != null ? tariffs.find((item) => item.id === selectedTariffId) ?? null : null),
    [selectedTariffId, tariffs],
  );

  const loadSummary = useCallback(async (clientId: number) => {
    try {
      setLoadingSummary(true);
      const data = await fetchAdminClientBalanceSummary(clientId);
      setSummary(data);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить баланс'));
    } finally {
      setLoadingSummary(false);
    }
  }, []);

  const loadOps = useCallback(async (clientId: number, p: number, s = pageSize) => {
    try {
      setLoadingOps(true);
      const offset = (p - 1) * s;
      const resp = await fetchAdminClientBalanceOps(clientId, { offset, limit: s });
      setOps(resp.items);
      setTotalOps(resp.total);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить операции'));
    } finally {
      setLoadingOps(false);
    }
  }, [pageSize]);

  const loadTariffs = useCallback(async (clientId: number, preferredTariffId?: number | null) => {
    try {
      setLoadingTariffs(true);
      const resp = await fetchAdminClientTariffs(clientId, { offset: 0, limit: 100 });
      setTariffs(resp.items);
      setTotalTariffs(resp.total);
      const nextTariffId = preferredTariffId != null && resp.items.some((item) => item.id === preferredTariffId)
        ? preferredTariffId
        : (resp.items[0]?.id ?? null);
      setSelectedTariffId(nextTariffId);
      return nextTariffId;
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить тарифы'));
      setTariffs([]);
      setTotalTariffs(0);
      setSelectedTariffId(null);
      return null;
    } finally {
      setLoadingTariffs(false);
    }
  }, []);

  const loadTariffOps = useCallback(async (tariffId: number, p: number, s = tariffOpsPageSize) => {
    try {
      setLoadingTariffOps(true);
      const offset = (p - 1) * s;
      const resp = await fetchAdminTariffOps(tariffId, { offset, limit: s });
      setTariffOps(resp.items);
      setTotalTariffOps(resp.total);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить историю тарифа'));
    } finally {
      setLoadingTariffOps(false);
    }
  }, [tariffOpsPageSize]);

  useEffect(() => {
    if (!hasClient || selectedClientId == null) return;
    setError(null);
    setPage(1);
    setTariffOpsPage(1);
    void loadSummary(selectedClientId);
    void loadOps(selectedClientId, 1, pageSize);
    void loadTariffs(selectedClientId);
  }, [selectedClientId, hasClient, loadSummary, loadOps, loadTariffs, pageSize]);

  useEffect(() => {
    if (selectedTariffId == null) {
      setTariffOps([]);
      setTotalTariffOps(0);
      return;
    }
    void loadTariffOps(selectedTariffId, tariffOpsPage, tariffOpsPageSize);
  }, [selectedTariffId, tariffOpsPage, tariffOpsPageSize, loadTariffOps]);

  const totalPages = Math.max(1, Math.ceil(totalOps / pageSize));
  const totalTariffOpsPages = Math.max(1, Math.ceil(totalTariffOps / tariffOpsPageSize));
  const currentTariffAmount = useMemo(
    () => (tariffs.length > 0 ? tariffs[0].currentAmount : null),
    [tariffs],
  );

  async function handleTariffModalDone(nextTariffId?: number) {
    if (selectedClientId == null) return;
    setTariffModal(null);
    const actualTariffId = await loadTariffs(selectedClientId, nextTariffId ?? selectedTariffId);
    setTariffOpsPage(1);
    if (actualTariffId != null) {
      await loadTariffOps(actualTariffId, 1, tariffOpsPageSize);
    }
  }

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
                {u.name || u.login} (id: {u.id})
              </option>
            ))}
          </select>
        </div>
        <div className="actions">
          {!hasClient && <span className="sub">Выберите клиента, чтобы увидеть баланс и тарифы</span>}
          {loadingSummary && <span className="sub">Загрузка…</span>}
        </div>
      </div>

      {error && <div className="sub" style={{ color: '#d00' }}>{error}</div>}

      {hasClient && summary && (
        <div style={{ overflowX: 'auto' }}>
          <div className="table-card" style={{ display: 'grid', gap: 12, minWidth: 720, padding: '12px 16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontWeight: 600 }}>Сводка по клиенту</div>
                <div className="sub">Клиент id: {summary.clientId}</div>
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
                <div style={{ fontSize: 20, fontWeight: 600, color: summary.debt ? '#d23' : '#111' }}>{summary.remaining}</div>
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
                <div className="sub">Текущий тариф</div>
                <div>{loadingTariffs ? '...' : (currentTariffAmount ?? '-')}</div>
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
                <span className="sub">Операции по балансу</span>
              </div>
              <div className="actions">
                {loadingOps ? <span className="sub">Загрузка…</span> : <span className="sub">Всего: {totalOps}</span>}
              </div>
            </div>
            <div className="table-footer table-footer--top">
              Показано {ops.length} из {totalOps}
              <div className="spacer" />
              <div className="pager">
                <button className="pager__btn" disabled={page <= 1} onClick={() => {
                  const p = Math.max(1, page - 1);
                  setPage(p);
                  if (selectedClientId != null) void loadOps(selectedClientId, p);
                }}>‹</button>
                <span className="pager__info">{page} / {totalPages}</span>
                <button className="pager__btn" disabled={page >= totalPages} onClick={() => {
                  const p = Math.min(totalPages, page + 1);
                  setPage(p);
                  if (selectedClientId != null) void loadOps(selectedClientId, p);
                }}>›</button>
                <select className="pager__size" value={pageSize} onChange={(e) => {
                  const s = Number(e.target.value);
                  setPageSize(s);
                  setPage(1);
                  if (selectedClientId != null) void loadOps(selectedClientId, 1, s);
                }}>
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
                      <td className="muted" style={{ whiteSpace: 'nowrap' }}><DateTimeCompact value={op.createdAt} /></td>
                      <td>
                        <span className={op.type === 'credit' ? 'badge badge--green' : 'badge badge--orange'} style={{ textTransform: 'capitalize' }}>
                          {op.type === 'credit' ? 'Начисление' : 'Списание'}
                        </span>
                      </td>
                      <td>{op.amount}</td>
                      <td>{op.comment || '—'}</td>
                      <td className="muted">{op.createdBy.name || op.createdBy.login} (id: {op.createdBy.id})</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {hasClient && (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ overflowX: 'auto' }}>
            <div className="table-card" style={{ minWidth: 940, padding: '0 8px 8px' }}>
              <div className="table-toolbar">
                <div className="filters">
                  <span className="sub">Тарифы</span>
                </div>
                <div className="actions" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  {loadingTariffs ? <span className="sub">Загрузка…</span> : <span className="sub">Всего: {totalTariffs}</span>}
                  <button className="btn btn--primary" type="button" onClick={() => setTariffModal({ mode: 'create' })}>
                    Добавить тариф
                  </button>
                </div>
              </div>
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Создан</th>
                      <th>База</th>
                      <th>Текущее значение</th>
                      <th>Комментарий</th>
                      <th>Создал</th>
                      <th>Действия</th>
                    </tr>
                  </thead>
                  <tbody>
                    {!loadingTariffs && tariffs.length === 0 && (
                      <tr>
                        <td colSpan={7} className="muted" style={{ padding: 16 }}>Тарифы ещё не созданы.</td>
                      </tr>
                    )}
                    {tariffs.map((tariff) => (
                      <tr
                        key={tariff.id}
                        style={{ backgroundColor: selectedTariffId === tariff.id ? '#f7f8fc' : undefined, cursor: 'pointer' }}
                        onClick={() => {
                          setSelectedTariffId(tariff.id);
                          setTariffOpsPage(1);
                        }}
                      >
                        <td>{tariff.id}</td>
                        <td className="muted" style={{ whiteSpace: 'nowrap' }}><DateTimeCompact value={tariff.createdAt} /></td>
                        <td>{tariff.baseAmount}</td>
                        <td style={{ fontWeight: 600 }}>{tariff.currentAmount}</td>
                        <td>{tariff.comment || '—'}</td>
                        <td className="muted">{tariff.createdBy.name || tariff.createdBy.login} (id: {tariff.createdBy.id})</td>
                        <td>
                          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                            <button className="btn btn--ghost" type="button" onClick={(e) => {
                              e.stopPropagation();
                              setSelectedTariffId(tariff.id);
                              setTariffOpsPage(1);
                            }}>
                              История
                            </button>
                            <button className="btn btn--secondary" type="button" onClick={(e) => {
                              e.stopPropagation();
                              setTariffModal({ mode: 'credit', tariff });
                            }}>
                              Добавить
                            </button>
                            <button className="btn btn--secondary" type="button" onClick={(e) => {
                              e.stopPropagation();
                              setTariffModal({ mode: 'debit', tariff });
                            }}>
                              Списать
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {selectedTariff && (
            <div style={{ overflowX: 'auto' }}>
              <div className="table-card" style={{ minWidth: 900, padding: '0 8px 8px' }}>
                <div className="table-toolbar">
                  <div className="filters">
                    <span className="sub">История тарифа #{selectedTariff.id}</span>
                  </div>
                  <div className="actions">
                    {loadingTariffOps ? <span className="sub">Загрузка…</span> : <span className="sub">Всего: {totalTariffOps}</span>}
                  </div>
                </div>
                <div style={{ padding: '0 8px 8px' }} className="sub">
                  Текущее значение: <b>{selectedTariff.currentAmount}</b>, базовый размер: <b>{selectedTariff.baseAmount}</b>
                </div>
                <div className="table-footer table-footer--top">
                  Показано {tariffOps.length} из {totalTariffOps}
                  <div className="spacer" />
                  <div className="pager">
                    <button className="pager__btn" disabled={tariffOpsPage <= 1} onClick={() => {
                      const p = Math.max(1, tariffOpsPage - 1);
                      setTariffOpsPage(p);
                    }}>‹</button>
                    <span className="pager__info">{tariffOpsPage} / {totalTariffOpsPages}</span>
                    <button className="pager__btn" disabled={tariffOpsPage >= totalTariffOpsPages} onClick={() => {
                      const p = Math.min(totalTariffOpsPages, tariffOpsPage + 1);
                      setTariffOpsPage(p);
                    }}>›</button>
                    <select className="pager__size" value={tariffOpsPageSize} onChange={(e) => {
                      const s = Number(e.target.value);
                      setTariffOpsPageSize(s);
                      setTariffOpsPage(1);
                    }}>
                      <option value={10}>10</option>
                      <option value={25}>25</option>
                      <option value={50}>50</option>
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
                      {!loadingTariffOps && tariffOps.length === 0 && (
                        <tr>
                          <td colSpan={5} className="muted" style={{ padding: 16 }}>Изменений по тарифу нет.</td>
                        </tr>
                      )}
                      {tariffOps.map((op) => (
                        <tr key={op.id}>
                          <td className="muted" style={{ whiteSpace: 'nowrap' }}><DateTimeCompact value={op.createdAt} /></td>
                          <td>
                            <span className={op.type === 'credit' ? 'badge badge--green' : 'badge badge--orange'}>
                              {op.type === 'credit' ? 'Добавление' : 'Списание'}
                            </span>
                          </td>
                          <td>{op.amount}</td>
                          <td>{op.comment}</td>
                          <td className="muted">{op.createdBy.name || op.createdBy.login} (id: {op.createdBy.id})</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {modal && selectedClientId != null && (
        <OperationModal
          clientId={selectedClientId}
          type={modal.type}
          onClose={() => setModal(null)}
          onDone={() => {
            setModal(null);
            void loadSummary(selectedClientId);
            void loadOps(selectedClientId, 1, pageSize);
            setPage(1);
          }}
        />
      )}

      {tariffModal && selectedClientId != null && (
        <TariffModal
          clientId={selectedClientId}
          state={tariffModal}
          onClose={() => setTariffModal(null)}
          onDone={(nextTariffId) => {
            void handleTariffModalDone(nextTariffId);
          }}
        />
      )}
    </div>
  );
}

export default AdminBalance;


