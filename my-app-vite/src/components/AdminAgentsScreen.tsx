import { useEffect, useMemo, useState } from 'react';
import DateTimeCompact from './DateTimeCompact';
import TariffManagerModal from './TariffManagerModal';
import {
  createAdminAgent,
  createAdminAgentTariff,
  fetchAdminAgentBalanceOps,
  fetchAdminAgentBalanceSummary,
  fetchAdminAgentTariffs,
  fetchAdminAgents,
  fetchAdminClientsSummary,
  fetchAdminTariffOps,
  createAdminTariffOp,
  updateAdminAgent,
  type AdminAgentCreateResp,
  type AdminAgentSummaryItem,
  type AdminClientSummaryItem,
  type BalanceOperation,
} from '../api';

type AdminAgentsScreenProps = {
  onOpenAgentClients?: (agentId: number, agentName: string) => void;
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function getToday(): string {
  return new Date().toISOString().slice(0, 10);
}

type AgentEditModalProps = {
  mode: 'create' | 'edit';
  initialName?: string;
  initialLogin?: string;
  initialDisabled?: boolean;
  onClose: () => void;
  onDone: (resp: AdminAgentCreateResp | { user: { isDisabled?: boolean | null } }) => void;
  agentId?: number;
};

function AgentEditModal({ mode, initialName = '', initialLogin = '', initialDisabled = false, onClose, onDone, agentId }: AgentEditModalProps) {
  const [name, setName] = useState(initialName);
  const [login, setLogin] = useState(initialLogin);
  const [password, setPassword] = useState('');
  const [isDisabled, setIsDisabled] = useState(initialDisabled);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError('Укажите имя агента');
      return;
    }
    try {
      setLoading(true);
      setError(null);
      if (mode === 'create') {
        const resp = await createAdminAgent({
          name: name.trim(),
          login: login.trim() || undefined,
          password: password.trim() || undefined,
        });
        onDone(resp);
        return;
      }
      if (!agentId) return;
      const resp = await updateAdminAgent(agentId, {
        name: name.trim(),
        login: login.trim() || undefined,
        password: password.trim() || undefined,
        isDisabled,
      });
      onDone(resp);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось сохранить агента'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal" style={{ maxWidth: 520, borderRadius: 12, padding: 0, overflow: 'hidden' }}>
        <div className="modal__header" style={{ padding: '14px 16px', borderBottom: '1px solid #eee' }}>
          <div style={{ fontWeight: 600 }}>{mode === 'create' ? 'Новый агент' : 'Редактирование агента'}</div>
        </div>
        <form onSubmit={handleSubmit} className="modal__body" style={{ display: 'grid', gap: 12, padding: '16px' }}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="sub">Имя агента</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="sub">Логин</span>
            <input value={login} onChange={(e) => setLogin(e.target.value)} />
          </label>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="sub">{mode === 'create' ? 'Пароль' : 'Новый пароль'}</span>
            <input value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {mode === 'edit' && (
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <input type="checkbox" checked={isDisabled} onChange={(e) => setIsDisabled(e.target.checked)} />
              <span>Агент отключён</span>
            </label>
          )}
          {error && <div className="sub" style={{ color: '#d00' }}>{error}</div>}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button type="button" className="btn btn--ghost" onClick={onClose}>Отмена</button>
            <button type="submit" className="btn btn--primary" disabled={loading}>
              {loading ? 'Сохранение…' : 'Сохранить'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function AdminAgentsScreen({ onOpenAgentClients }: AdminAgentsScreenProps) {
  const [agents, setAgents] = useState<AdminAgentSummaryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null);
  const [agentOps, setAgentOps] = useState<BalanceOperation[]>([]);
  const [agentBalance, setAgentBalance] = useState<number | null>(null);
  const [ownedClients, setOwnedClients] = useState<AdminClientSummaryItem[]>([]);
  const [editModal, setEditModal] = useState<{ mode: 'create' | 'edit'; agent?: AdminAgentSummaryItem } | null>(null);
  const [tariffModalOpen, setTariffModalOpen] = useState(false);

  const selectedAgent = useMemo(
    () => (selectedAgentId != null ? agents.find((item) => item.user.id === selectedAgentId) ?? null : null),
    [agents, selectedAgentId],
  );

  async function loadData(preferredAgentId?: number | null) {
    try {
      setLoading(true);
      setError(null);
      const [agentsResp, clientsResp] = await Promise.all([
        fetchAdminAgents(),
        fetchAdminClientsSummary({ fromDate: getToday(), toDate: getToday() }),
      ]);
      setAgents(agentsResp.items);
      const nextAgentId = preferredAgentId ?? selectedAgentId ?? agentsResp.items[0]?.user.id ?? null;
      setSelectedAgentId(nextAgentId);
      if (nextAgentId != null) {
        const [balanceResp, opsResp] = await Promise.all([
          fetchAdminAgentBalanceSummary(nextAgentId),
          fetchAdminAgentBalanceOps(nextAgentId, { offset: 0, limit: 100 }),
        ]);
        setAgentBalance(balanceResp.remaining);
        setAgentOps(opsResp.items);
        setOwnedClients(clientsResp.items.filter((item) => item.ownerType === 'agent' && item.ownerUser?.id === nextAgentId));
      } else {
        setAgentBalance(null);
        setAgentOps([]);
        setOwnedClients([]);
      }
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить агентов'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedAgentId == null) return;
    void loadData(selectedAgentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAgentId]);

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="table-card">
        <div className="table-toolbar">
          <div className="filters">
            <span className="sub">Всего агентов: {agents.length}</span>
          </div>
          <div className="actions">
            <button type="button" className="btn btn--primary" onClick={() => setEditModal({ mode: 'create' })}>
              + Новый агент
            </button>
          </div>
        </div>
        {error && <div className="sub" style={{ color: '#d00', padding: '0 16px 12px' }}>{error}</div>}
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Агент</th>
                <th>Статус</th>
                <th>Баланс</th>
                <th>Тариф</th>
                <th>Клиентов</th>
                <th>Создан</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {!loading && agents.length === 0 && (
                <tr>
                  <td colSpan={7} className="muted" style={{ padding: 16 }}>Агенты пока не созданы.</td>
                </tr>
              )}
              {agents.map((agent) => (
                <tr
                  key={agent.user.id}
                  style={{ backgroundColor: selectedAgentId === agent.user.id ? '#f7f8fc' : undefined, cursor: 'pointer' }}
                  onClick={() => setSelectedAgentId(agent.user.id)}
                >
                  <td>
                    <div className="name">{agent.user.name || agent.user.login}</div>
                    <div className="sub">{agent.user.login} (id: {agent.user.id})</div>
                  </td>
                  <td>
                    <span className={agent.user.isDisabled ? 'badge badge--orange' : 'badge badge--green'}>
                      {agent.user.isDisabled ? 'Отключён' : 'Активен'}
                    </span>
                  </td>
                  <td>{agent.balance}</td>
                  <td>{agent.tariffAmount ?? '-'}</td>
                  <td>{agent.clientCount}</td>
                  <td className="muted"><DateTimeCompact value={agent.createdAt} /></td>
                  <td>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditModal({ mode: 'edit', agent });
                        }}
                      >
                        Редактировать
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {selectedAgent && (
        <div className="table-card" style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>{selectedAgent.user.name || selectedAgent.user.login}</div>
              <div className="sub">{selectedAgent.user.login} (id: {selectedAgent.user.id})</div>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button type="button" className="btn btn--primary" onClick={() => setTariffModalOpen(true)}>
                Управление тарифами
              </button>
              <button
                type="button"
                className="btn btn--secondary"
                onClick={() => setEditModal({ mode: 'edit', agent: selectedAgent })}
              >
                Настроить
              </button>
              {onOpenAgentClients && (
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={() => onOpenAgentClients(selectedAgent.user.id, selectedAgent.user.name || selectedAgent.user.login)}
                >
                  Открыть клиентов агента
                </button>
              )}
            </div>
          </div>

          <div className="summary-grid">
            <div className="summary-card">
              <div className="sub">Текущий баланс</div>
              <div className={`value${(agentBalance ?? 0) < 0 ? ' value--negative' : ''}`}>{agentBalance ?? '—'}</div>
            </div>
            <div className="summary-card">
              <div className="sub">Текущий тариф</div>
              <div className="value">{selectedAgent.tariffAmount ?? '—'}</div>
            </div>
            <div className="summary-card">
              <div className="sub">Начислено</div>
              <div className="value">{selectedAgent.credited}</div>
            </div>
            <div className="summary-card">
              <div className="sub">Списано</div>
              <div className="value">{selectedAgent.debited}</div>
            </div>
            <div className="summary-card">
              <div className="sub">Клиентов</div>
              <div className="value">{ownedClients.length}</div>
            </div>
          </div>

          <div style={{ overflowX: 'auto' }}>
            <div className="table-card" style={{ minWidth: 760, padding: '0 8px 8px' }}>
              <div className="table-toolbar">
                <div className="filters">
                  <span className="sub">Клиенты агента</span>
                </div>
              </div>
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Клиент</th>
                      <th>Остаток</th>
                      <th>Тариф</th>
                      <th>Проектов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ownedClients.length === 0 && (
                      <tr>
                        <td colSpan={4} className="muted" style={{ padding: 16 }}>У агента пока нет клиентов.</td>
                      </tr>
                    )}
                    {ownedClients.map((client) => (
                      <tr key={client.user.id}>
                        <td>
                          <div className="name">{client.profile?.name || client.user.name || client.user.login}</div>
                          <div className="sub">{client.user.login} (id: {client.user.id})</div>
                        </td>
                        <td>{client.remaining}</td>
                        <td>{client.tariffAmount ?? '-'}</td>
                        <td>{client.projectCount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div style={{ overflowX: 'auto' }}>
            <div className="table-card" style={{ minWidth: 760, padding: '0 8px 8px' }}>
              <div className="table-toolbar">
                <div className="filters">
                  <span className="sub">История операций по балансу агента</span>
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
                    {agentOps.length === 0 && (
                      <tr>
                        <td colSpan={5} className="muted" style={{ padding: 16 }}>Операций пока нет.</td>
                      </tr>
                    )}
                    {agentOps.map((op) => (
                      <tr key={op.id}>
                        <td className="muted"><DateTimeCompact value={op.createdAt} /></td>
                        <td>
                          <span className={op.type === 'credit' ? 'badge badge--green' : 'badge badge--orange'}>
                            {op.type === 'credit' ? 'Начисление' : 'Списание'}
                          </span>
                        </td>
                        <td>{op.amount}</td>
                        <td>{op.comment || '—'}</td>
                        <td className="muted">{op.createdBy.name || op.createdBy.login}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {editModal && (
        <AgentEditModal
          mode={editModal.mode}
          agentId={editModal.agent?.user.id}
          initialName={editModal.agent?.user.name || editModal.agent?.user.login}
          initialLogin={editModal.agent?.user.login}
          initialDisabled={Boolean(editModal.agent?.user.isDisabled)}
          onClose={() => setEditModal(null)}
          onDone={() => {
            setEditModal(null);
            void loadData(selectedAgentId);
          }}
        />
      )}

      {tariffModalOpen && selectedAgent && (
        <TariffManagerModal
          targetId={selectedAgent.user.id}
          title={`Тарифы агента #${selectedAgent.user.id}`}
          onClose={() => setTariffModalOpen(false)}
          onChanged={() => loadData(selectedAgent.user.id)}
          fetchTariffs={fetchAdminAgentTariffs}
          createTariff={createAdminAgentTariff}
          fetchTariffOps={fetchAdminTariffOps}
          createTariffOp={createAdminTariffOp}
        />
      )}
    </div>
  );
}

export default AdminAgentsScreen;
