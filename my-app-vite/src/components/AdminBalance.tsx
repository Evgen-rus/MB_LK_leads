import ClientOptions from './ClientOptions';
import { useCallback, useEffect, useMemo, useState } from 'react';
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
  updateAdminTariff,
  type UserInfo,
  type BalanceOperation,
  type ClientBalanceSummary,
} from '../api';

type AdminBalanceProps = {
  managerRole?: 'admin' | 'agent';
  initialClientId?: number | null;
  initialClientName?: string | null;
  initialModalType?: 'tariff' | null;
};

type BalanceJournalModalProps = {
  clientId: number;
  ops: BalanceOperation[];
  totalOps: number;
  loading: boolean;
  page: number;
  pageSize: number;
  totalPages: number;
  onClose: () => void;
  onPrevPage: () => void;
  onNextPage: () => void;
  onPageSizeChange: (nextSize: number) => void;
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function BalanceJournalModal({
  clientId,
  ops,
  totalOps,
  loading,
  page,
  pageSize,
  totalPages,
  onClose,
  onPrevPage,
  onNextPage,
  onPageSizeChange,
}: BalanceJournalModalProps) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1300,
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="modal-card"
        style={{
          background: '#fff',
          borderRadius: 10,
          width: '100%',
          maxWidth: 1040,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
          <div style={{ display: 'grid', gap: 6 }}>
            <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>Системный журнал баланса</div>
            <div className="sub">Клиент id: {clientId}</div>
          </div>
          <button type="button" className="btn btn--ghost" onClick={onClose} style={{ padding: '6px 10px' }}>
            ✕
          </button>
        </div>

        <div style={{ padding: 20, display: 'grid', gap: 12 }}>
          <div
            style={{
              display: 'grid',
              gap: 8,
              padding: 12,
              border: '1px solid #eee',
              borderRadius: 8,
              background: '#fafbff',
            }}
          >
            <div style={{ fontWeight: 600 }}>Технический уровень учёта</div>
            <div className="sub">
              Здесь видны системные движения баланса: зеркалирование тарифов, служебные начисления и списания,
              а также операции, влияющие на расчёт остатка клиента.
            </div>
          </div>

          <div className="table-card" style={{ minWidth: 860, padding: '0 8px 8px' }}>
            <div className="table-toolbar">
              <div className="filters">
                <span className="sub">Записи журнала</span>
              </div>
              <div className="actions">
                {loading ? <span className="sub">Загрузка…</span> : <span className="sub">Всего: {totalOps}</span>}
              </div>
            </div>
            <div className="table-footer table-footer--top">
              Показано {ops.length} из {totalOps}
              <div className="spacer" />
              <div className="pager">
                <button className="pager__btn" disabled={page <= 1} onClick={onPrevPage}>‹</button>
                <span className="pager__info">{page} / {totalPages}</span>
                <button className="pager__btn" disabled={page >= totalPages} onClick={onNextPage}>›</button>
                <select className="pager__size" value={pageSize} onChange={(e) => onPageSizeChange(Number(e.target.value))}>
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
                  {!loading && ops.length === 0 && (
                    <tr>
                      <td colSpan={5} className="muted" style={{ padding: 16 }}>Записей журнала нет.</td>
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
      </div>
    </div>
  );
}

function AdminBalance({
  managerRole = 'admin',
  initialClientId = null,
  initialClientName = null,
  initialModalType = null,
}: AdminBalanceProps) {
  const isAgentManager = managerRole === 'agent';
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
  const [journalOpen, setJournalOpen] = useState(false);

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
  const selectedClient = useMemo(
    () => (selectedClientId != null ? users.find((user) => user.id === selectedClientId) ?? null : null),
    [selectedClientId, users],
  );
  const selectedClientName =
    selectedClient?.name
    || selectedClient?.login
    || (selectedClientId === initialClientId ? initialClientName : null)
    || (selectedClientId != null ? `Клиент #${selectedClientId}` : '');
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
      loadCurrentTariff(clientId),
    ]);
  }, [loadCurrentTariff, loadSummary]);

  useEffect(() => {
    if (!hasClient || selectedClientId == null) return;
    void refreshClientData(selectedClientId);
  }, [hasClient, refreshClientData, selectedClientId]);

  useEffect(() => {
    setJournalOpen(false);
    setOps([]);
    setTotalOps(0);
    setPage(1);
  }, [selectedClientId]);

  useEffect(() => {
    if (!journalOpen || selectedClientId == null) return;
    void loadOps(selectedClientId, page, pageSize);
  }, [journalOpen, selectedClientId, page, pageSize, loadOps]);

  return (
    <div className="table-card admin-balance">
      <div className="table-toolbar toolbar-split admin-balance__toolbar">
        <div className="filters toolbar-left">
          <select
            value={selectedClientId ?? ''}
            onChange={(e) => {
              const nextValue = e.target.value ? Number(e.target.value) : null;
              setSelectedClientId(nextValue);
            }}
          >
            <option value="">Выберите клиента</option>
            <ClientOptions clients={users} />
          </select>
        </div>
        <div className="actions toolbar-right admin-balance__toolbar-actions">
          {!hasClient && <span className="sub">Выберите клиента, чтобы увидеть баланс и тарифы</span>}
          {(loadingSummary || loadingTariffSummary) && <span className="sub">Загрузка…</span>}
        </div>
      </div>

      {error && <div className="sub" style={{ color: '#d00' }}>{error}</div>}

      {hasClient && summary && (
        <div className="table-card admin-balance__section">
          <div className="admin-balance__section-header">
            <div>
              <div style={{ fontWeight: 600 }}>Сводка по клиенту</div>
              <div className="sub">Клиент id: {summary.clientId}</div>
            </div>
            <button className="btn btn--primary" type="button" onClick={() => setTariffManagerOpen(true)}>
              {isAgentManager ? 'Просмотр тарифов' : 'Управление тарифами'}
            </button>
          </div>
          <div className="admin-balance__stats">
            <div className="admin-balance__stat">
              <div className="sub">Текущий остаток</div>
              <div className="admin-balance__stat-value" style={{ color: summary.debt ? '#d23' : '#111' }}>{summary.remaining}</div>
              {summary.debt && <div className="sub" style={{ color: '#d23' }}>Долг</div>}
            </div>
            <div className="admin-balance__stat">
              <div className="sub">Начислено всего</div>
              <div>{summary.credited}</div>
            </div>
            <div className="admin-balance__stat">
              <div className="sub">Списано всего</div>
              <div>{summary.debited}</div>
            </div>
            <div className="admin-balance__stat">
              <div className="sub">Выдано номеров (всего)</div>
              <div>{summary.usedTotal}</div>
            </div>
            <div className="admin-balance__stat">
              <div className="sub">Текущий тариф</div>
              <div>{loadingTariffSummary ? '...' : (currentTariffAmount ?? '-')}</div>
            </div>
          </div>
        </div>
      )}

      {hasClient && (
        <div className="table-card admin-balance__section">
          <div className="admin-balance__section-header">
            <div>
              <div style={{ fontWeight: 600 }}>Системный журнал баланса</div>
              <div className="sub">
                Технический журнал движений баланса: зеркалирование тарифов, служебные начисления и списания.
              </div>
            </div>
            <div className="admin-balance__section-actions">
              {totalOps > 0 && <span className="sub">Последняя загрузка: {totalOps} записей</span>}
              <button className="btn btn--secondary" type="button" onClick={() => setJournalOpen(true)}>
                Открыть журнал
              </button>
            </div>
          </div>
        </div>
      )}

      {tariffManagerOpen && selectedClientId != null && (
        <TariffManagerModal
          targetId={selectedClientId}
          targetName={selectedClientName}
          title={`Тарифы клиента: ${selectedClientName}`}
          onClose={() => setTariffManagerOpen(false)}
          onChanged={() => refreshClientData(selectedClientId)}
          readOnly={isAgentManager}
          fetchTariffs={fetchAdminClientTariffs}
          createTariff={createAdminClientTariff}
          updateTariff={updateAdminTariff}
          createTariffOp={createAdminTariffOp}
          fetchTariffOps={fetchAdminTariffOps}
        />
      )}

      {journalOpen && selectedClientId != null && (
        <BalanceJournalModal
          clientId={selectedClientId}
          ops={ops}
          totalOps={totalOps}
          loading={loadingOps}
          page={page}
          pageSize={pageSize}
          totalPages={totalPages}
          onClose={() => setJournalOpen(false)}
          onPrevPage={() => setPage((prev) => Math.max(1, prev - 1))}
          onNextPage={() => setPage((prev) => Math.min(totalPages, prev + 1))}
          onPageSizeChange={(nextSize) => {
            setPageSize(nextSize);
            setPage(1);
          }}
        />
      )}
    </div>
  );
}

export default AdminBalance;
