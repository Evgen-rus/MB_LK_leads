import { useCallback, useEffect, useState } from 'react';
import DateTimeCompact from './DateTimeCompact';
import TariffManagerModal from './TariffManagerModal';
import {
  fetchAdminUsers,
  fetchAdminClientBalanceSummary,
  fetchAdminClientBalanceOps,
  fetchAdminClientTariffs,
  fetchAdminTariffOps,
  createAdminClientTariff,
  createAdminTariffOp,
  type UserInfo,
  type BalanceOperation,
  type ClientBalanceSummary,
} from '../api';

type AdminBalanceProps = {
  initialClientId?: number | null;
  initialModalType?: 'tariff' | null;
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
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
  const [loadingTariffSummary, setLoadingTariffSummary] = useState(false);
  const [currentTariffAmount, setCurrentTariffAmount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tariffManagerOpen, setTariffManagerOpen] = useState(initialModalType === 'tariff');

  useEffect(() => {
    (async () => {
      try {
        const list = await fetchAdminUsers();
        setUsers(list);
      } catch (err) {
        console.error(err);
      }
    })();
  }, []);

  useEffect(() => {
    if (initialClientId != null) {
      setSelectedClientId(initialClientId);
    }
    if (initialModalType === 'tariff') {
      setTariffManagerOpen(true);
    }
  }, [initialClientId, initialModalType]);

  const hasClient = selectedClientId != null;
  const totalPages = Math.max(1, Math.ceil(totalOps / pageSize));

  const loadSummary = useCallback(async (clientId: number) => {
    try {
      setLoadingSummary(true);
      setError(null);
      const data = await fetchAdminClientBalanceSummary(clientId);
      setSummary(data);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить баланс'));
      setSummary(null);
    } finally {
      setLoadingSummary(false);
    }
  }, []);

  const loadOps = useCallback(async (clientId: number, nextPage: number, nextPageSize = pageSize) => {
    try {
      setLoadingOps(true);
      setError(null);
      const offset = (nextPage - 1) * nextPageSize;
      const resp = await fetchAdminClientBalanceOps(clientId, { offset, limit: nextPageSize });
      setOps(resp.items);
      setTotalOps(resp.total);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить операции'));
      setOps([]);
      setTotalOps(0);
    } finally {
      setLoadingOps(false);
    }
  }, [pageSize]);

  const loadCurrentTariff = useCallback(async (clientId: number) => {
    try {
      setLoadingTariffSummary(true);
      const resp = await fetchAdminClientTariffs(clientId, { offset: 0, limit: 1 });
      setCurrentTariffAmount(resp.items[0]?.currentAmount ?? null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить тариф'));
      setCurrentTariffAmount(null);
    } finally {
      setLoadingTariffSummary(false);
    }
  }, []);

  const refreshClientData = useCallback(async (clientId: number) => {
    await Promise.all([
      loadSummary(clientId),
      loadOps(clientId, 1, pageSize),
      loadCurrentTariff(clientId),
    ]);
    setPage(1);
  }, [loadCurrentTariff, loadOps, loadSummary, pageSize]);

  useEffect(() => {
    if (!hasClient || selectedClientId == null) return;
    void refreshClientData(selectedClientId);
  }, [hasClient, refreshClientData, selectedClientId]);

  return (
    <div className="table-card" style={{ display: 'grid', gap: 16 }}>
      <div className="table-toolbar" style={{ gap: 12 }}>
        <div className="filters" style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <select
            value={selectedClientId ?? ''}
            onChange={(e) => {
              const nextValue = e.target.value ? Number(e.target.value) : null;
              setSelectedClientId(nextValue);
            }}
          >
            <option value="">Выберите клиента</option>
            {users.map((user) => (
              <option key={user.id} value={user.id}>
                {user.name || user.login} (id: {user.id})
              </option>
            ))}
          </select>
        </div>
        <div className="actions">
          {!hasClient && <span className="sub">Выберите клиента, чтобы увидеть баланс и тарифы</span>}
          {(loadingSummary || loadingTariffSummary) && <span className="sub">Загрузка…</span>}
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
              <button className="btn btn--primary" type="button" onClick={() => setTariffManagerOpen(true)}>
                Управление тарифами
              </button>
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
                <div className="sub">Списано всего</div>
                <div>{summary.debited}</div>
              </div>
              <div>
                <div className="sub">Выдано номеров (всего)</div>
                <div>{summary.usedTotal}</div>
              </div>
              <div>
                <div className="sub">Текущий тариф</div>
                <div>{loadingTariffSummary ? '...' : (currentTariffAmount ?? '-')}</div>
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
                  const nextPage = Math.max(1, page - 1);
                  setPage(nextPage);
                  if (selectedClientId != null) void loadOps(selectedClientId, nextPage);
                }}>‹</button>
                <span className="pager__info">{page} / {totalPages}</span>
                <button className="pager__btn" disabled={page >= totalPages} onClick={() => {
                  const nextPage = Math.min(totalPages, page + 1);
                  setPage(nextPage);
                  if (selectedClientId != null) void loadOps(selectedClientId, nextPage);
                }}>›</button>
                <select className="pager__size" value={pageSize} onChange={(e) => {
                  const nextSize = Number(e.target.value);
                  setPageSize(nextSize);
                  setPage(1);
                  if (selectedClientId != null) void loadOps(selectedClientId, 1, nextSize);
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

      {tariffManagerOpen && selectedClientId != null && (
        <TariffManagerModal
          targetId={selectedClientId}
          title={`Тарифы клиента #${selectedClientId}`}
          onClose={() => setTariffManagerOpen(false)}
          onChanged={() => refreshClientData(selectedClientId)}
          fetchTariffs={fetchAdminClientTariffs}
          createTariff={createAdminClientTariff}
          fetchTariffOps={fetchAdminTariffOps}
          createTariffOp={createAdminTariffOp}
        />
      )}
    </div>
  );
}

export default AdminBalance;
