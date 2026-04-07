import { Fragment, useEffect, useMemo, useState } from 'react';
import DateTimeCompact from './DateTimeCompact';
import AdminClientCardModal from './AdminClientCardModal';
import {
  createAdminAgent,
  fetchAdminAgentBalanceOps,
  fetchAdminAgents,
  fetchAdminClientsSummary,
  impersonateClient,
  transferAdminClientOwner,
  updateAdminAgent,
  type AdminAgentCreateResp,
  type AdminAgentSummaryItem,
  type AdminClientSummaryItem,
  type BalanceOperation,
} from '../api';

type AdminAgentsScreenProps = {
  onOpenClientProjects?: (clientId: number, clientName: string) => void;
  onOpenClientChanges?: (clientId: number, clientName: string) => void;
  onOpenClientBlacklistChanges?: (clientId: number, clientName: string) => void;
  onOpenClientBalance?: (clientId: number, clientName: string, action: 'tariff') => void;
};

type ClientStatus = 'Активен' | 'Нет проектов' | 'Долг' | 'Дожим';

type AgentClientRow = {
  id: number;
  name: string;
  login: string;
  ownerType: 'admin' | 'agent';
  ownerUser?: { id: number; name: string; login: string } | null;
  projectCount: number;
  status: ClientStatus;
  tariffAmount?: number | null;
  remaining: number;
  totalVolume: number;
  totalLimit: number;
  usedTotal: number;
  pendingChanges: number;
  pendingCreates: number;
  pendingBlacklistAdds: number;
  pendingBlacklistDeletes: number;
  autoLimitControlEnabled: boolean;
  telegramNotificationsChatId?: string | null;
  telegramAutoPauseEnabled: boolean;
  uniqueProjectNamesEnabled: boolean;
  inn?: string | null;
  phone?: string | null;
  contact?: string | null;
};

const STATUS_COLORS: Record<ClientStatus, string> = {
  Активен: '#4CAF50',
  'Нет проектов': '#03A9F4',
  Долг: '#F44336',
  Дожим: '#7E57C2',
};

type AgentEditModalProps = {
  mode: 'create' | 'edit';
  initialName?: string;
  initialLogin?: string;
  initialDisabled?: boolean;
  onClose: () => void;
  onDone: (resp: AdminAgentCreateResp | { user: { isDisabled?: boolean | null } }) => void;
  agentId?: number;
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

function deriveStatus(row: AgentClientRow): ClientStatus {
  if (row.projectCount === 0) return 'Нет проектов';
  if (row.remaining <= 0) return 'Долг';
  const statusBase =
    typeof row.tariffAmount === 'number' && row.tariffAmount > 0
      ? row.tariffAmount
      : row.totalLimit || 0;
  if (statusBase > 0 && row.remaining <= statusBase * 0.3) return 'Дожим';
  return 'Активен';
}

function formatOwnerLabel(row: AgentClientRow): string {
  if (row.ownerType === 'agent' && row.ownerUser) return `${row.ownerUser.name} (агент)`;
  return 'Админ';
}

function mapSummaryItemToClientRow(it: AdminClientSummaryItem): AgentClientRow {
  const profile = it.profile;
  const displayName = profile?.name?.trim() || it.user.name?.trim() || it.user.login;
  const row: AgentClientRow = {
    id: it.user.id,
    name: displayName,
    login: it.user.login,
    ownerType: it.ownerType,
    ownerUser: it.ownerUser
      ? { id: it.ownerUser.id, name: it.ownerUser.name || it.ownerUser.login, login: it.ownerUser.login }
      : null,
    projectCount: it.projectCount,
    tariffAmount: it.tariffAmount ?? null,
    remaining: it.remaining,
    totalVolume: it.usedPeriod,
    totalLimit: it.totalLimit,
    usedTotal: it.usedTotal,
    pendingChanges: it.pendingChanges ?? 0,
    pendingCreates: it.pendingCreates ?? 0,
    pendingBlacklistAdds: 0,
    pendingBlacklistDeletes: 0,
    autoLimitControlEnabled: Boolean(it.autoLimitControlEnabled),
    telegramNotificationsChatId: it.user.telegramNotificationsChatId ?? null,
    telegramAutoPauseEnabled: Boolean(it.user.telegramAutoPauseEnabled),
    uniqueProjectNamesEnabled: Boolean(it.user.uniqueProjectNamesEnabled),
    inn: profile?.inn,
    phone: profile?.phone,
    contact: profile?.contact,
    status: 'Активен',
  };
  return { ...row, status: deriveStatus(row) };
}

function AgentEditModal({
  mode,
  initialName = '',
  initialLogin = '',
  initialDisabled = false,
  onClose,
  onDone,
  agentId,
}: AgentEditModalProps) {
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

function AdminAgentsScreen({
  onOpenClientProjects,
  onOpenClientChanges,
  onOpenClientBlacklistChanges,
  onOpenClientBalance,
}: AdminAgentsScreenProps) {
  const [agents, setAgents] = useState<AdminAgentSummaryItem[]>([]);
  const [agentOpsById, setAgentOpsById] = useState<Record<number, BalanceOperation[]>>({});
  const [agentClients, setAgentClients] = useState<AgentClientRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedAgentId, setExpandedAgentId] = useState<number | null>(null);
  const [selectedClientId, setSelectedClientId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [editModal, setEditModal] = useState<{ mode: 'create' | 'edit'; agent?: AdminAgentSummaryItem } | null>(null);
  const [cardClientId, setCardClientId] = useState<number | null>(null);
  const [cardClientData, setCardClientData] = useState<{
    name: string;
    inn?: string | null;
    phone?: string | null;
    contact?: string | null;
    telegramNotificationsChatId?: string | null;
    telegramAutoPauseEnabled: boolean;
    uniqueProjectNamesEnabled: boolean;
    login: string;
  } | null>(null);
  const [openingClientCabinetId, setOpeningClientCabinetId] = useState<number | null>(null);
  const [ownerTarget, setOwnerTarget] = useState<string>('admin');

  const expandedClients = useMemo(
    () => agentClients.filter((client) => client.ownerType === 'agent' && client.ownerUser?.id === expandedAgentId),
    [agentClients, expandedAgentId],
  );
  const filteredExpandedClients = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return expandedClients;
    return expandedClients.filter((client) =>
      client.name.toLowerCase().includes(q)
      || client.login.toLowerCase().includes(q)
      || String(client.id).includes(q),
    );
  }, [expandedClients, search]);
  const selectedClient = useMemo(
    () => (selectedClientId != null ? expandedClients.find((client) => client.id === selectedClientId) ?? null : null),
    [expandedClients, selectedClientId],
  );
  const selectedBlacklistTotal =
    selectedClient != null
      ? (selectedClient.pendingBlacklistAdds ?? 0) + (selectedClient.pendingBlacklistDeletes ?? 0)
      : 0;
  const selectedAccrued = selectedClient != null ? selectedClient.remaining + selectedClient.usedTotal : 0;

  async function loadData(preferredExpandedAgentId?: number | null, preferredClientId?: number | null) {
    try {
      setLoading(true);
      setError(null);
      const [agentsResp, clientsResp] = await Promise.all([
        fetchAdminAgents(),
        fetchAdminClientsSummary({ fromDate: getToday(), toDate: getToday() }),
      ]);
      const agentIds = agentsResp.items.map((agent) => agent.user.id);
      const opsEntries = await Promise.all(
        agentIds.map(async (agentId) => {
          const resp = await fetchAdminAgentBalanceOps(agentId, { offset: 0, limit: 100 });
          return [agentId, resp.items] as const;
        }),
      );
      const nextAgentClients = clientsResp.items.filter((item) => item.ownerType === 'agent').map(mapSummaryItemToClientRow);

      setAgents(agentsResp.items);
      setAgentClients(nextAgentClients);
      setAgentOpsById(Object.fromEntries(opsEntries));

      const requestedExpandedAgentId = preferredExpandedAgentId === undefined ? expandedAgentId : preferredExpandedAgentId;
      const nextExpandedAgentId =
        requestedExpandedAgentId != null && agentsResp.items.some((agent) => agent.user.id === requestedExpandedAgentId)
          ? requestedExpandedAgentId
          : null;
      setExpandedAgentId(nextExpandedAgentId);

      const nextExpandedClients = nextAgentClients.filter(
        (client) => client.ownerType === 'agent' && client.ownerUser?.id === nextExpandedAgentId,
      );
      if (nextExpandedAgentId == null) {
        setSelectedClientId(null);
      } else if (preferredClientId != null && nextExpandedClients.some((client) => client.id === preferredClientId)) {
        setSelectedClientId(preferredClientId);
      } else if (selectedClientId != null && nextExpandedClients.some((client) => client.id === selectedClientId)) {
        setSelectedClientId(selectedClientId);
      } else {
        setSelectedClientId(nextExpandedClients[0]?.id ?? null);
      }
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить агентов'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadData(null, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedClient) {
      setOwnerTarget('admin');
      return;
    }
    if (selectedClient.ownerType === 'agent' && selectedClient.ownerUser?.id) {
      setOwnerTarget(`agent:${selectedClient.ownerUser.id}`);
      return;
    }
    setOwnerTarget('admin');
  }, [selectedClient]);

  function toggleAgent(agentId: number) {
    setSearch('');
    if (expandedAgentId === agentId) {
      setExpandedAgentId(null);
      setSelectedClientId(null);
      return;
    }
    setExpandedAgentId(agentId);
    const nextClients = agentClients.filter((client) => client.ownerType === 'agent' && client.ownerUser?.id === agentId);
    setSelectedClientId(nextClients[0]?.id ?? null);
  }

  async function handleOpenClientCabinet(clientId: number) {
    if (!clientId) return;
    setOpeningClientCabinetId(clientId);
    setError(null);
    try {
      const resp = await impersonateClient(clientId);
      try {
        localStorage.setItem('access_token', resp.access_token);
        sessionStorage.setItem('access_token', resp.access_token);
        const parts = [`access_token=${encodeURIComponent(resp.access_token)}`, 'path=/', 'samesite=lax'];
        if (window.location.protocol === 'https:') parts.push('secure');
        document.cookie = parts.join('; ');
      } catch {
        /* ignore */
      }
      window.location.href = '/';
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось открыть ЛК клиента'));
      setOpeningClientCabinetId(null);
    }
  }

  async function handleTransferOwner() {
    if (!selectedClient) return;
    const payload =
      ownerTarget === 'admin'
        ? { ownerType: 'admin' as const }
        : { ownerType: 'agent' as const, agentId: Number(ownerTarget.split(':')[1]) };
    try {
      await transferAdminClientOwner(selectedClient.id, payload);
      window.dispatchEvent(new CustomEvent('app-toast', { detail: 'Владелец клиента обновлён' }));
      await loadData(payload.ownerType === 'agent' ? payload.agentId ?? null : expandedAgentId, selectedClient.id);
    } catch (err: unknown) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось переназначить клиента') }));
    }
  }

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
                <th>Клиентов</th>
                <th>Создан</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {!loading && agents.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted" style={{ padding: 16 }}>Агенты пока не созданы.</td>
                </tr>
              )}
              {agents.map((agent) => {
                const isExpanded = expandedAgentId === agent.user.id;
                const clientsCount = agentClients.filter(
                  (client) => client.ownerType === 'agent' && client.ownerUser?.id === agent.user.id,
                ).length;
                const agentOps = agentOpsById[agent.user.id] ?? [];
                return (
                  <Fragment key={agent.user.id}>
                    <tr
                      style={{ backgroundColor: isExpanded ? '#f7f8fc' : undefined, cursor: 'pointer' }}
                      onClick={() => toggleAgent(agent.user.id)}
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
                      <td>{agent.clientCount}</td>
                      <td className="muted"><DateTimeCompact value={agent.createdAt} /></td>
                      <td>
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          <button
                            type="button"
                            className="btn btn--secondary"
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleAgent(agent.user.id);
                            }}
                          >
                            {isExpanded ? 'Свернуть' : 'Клиенты'}
                          </button>
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
                    {isExpanded && (
                      <tr>
                        <td colSpan={6} style={{ padding: 0 }}>
                          <div style={{ padding: 16, background: '#fbfcff', borderTop: '1px solid #edf0f7' }}>
                            <div style={{ display: 'grid', gap: 16 }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                                <div>
                                  <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>{agent.user.name || agent.user.login}</div>
                                  <div className="sub">{agent.user.login} (id: {agent.user.id})</div>
                                </div>
                                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                  <button
                                    type="button"
                                    className="btn btn--primary"
                                    onClick={() => setEditModal({ mode: 'edit', agent })}
                                  >
                                    Настроить
                                  </button>
                                </div>
                              </div>

                              <div className="summary-grid">
                                <div className="summary-card">
                                  <div className="sub">Текущий баланс</div>
                                  <div className={`value${agent.balance < 0 ? ' value--negative' : ''}`}>{agent.balance}</div>
                                </div>
                                <div className="summary-card">
                                  <div className="sub">Начислено</div>
                                  <div className="value">{agent.credited}</div>
                                </div>
                                <div className="summary-card">
                                  <div className="sub">Списано</div>
                                  <div className="value">{agent.debited}</div>
                                </div>
                                <div className="summary-card">
                                  <div className="sub">Клиентов</div>
                                  <div className="value">{clientsCount}</div>
                                </div>
                              </div>

                              <div className="table-card" style={{ padding: 12 }}>
                                <div className="table-toolbar">
                                  <div className="filters">
                                    <span className="sub">Клиенты агента</span>
                                  </div>
                                  <div className="filters">
                                    <input
                                      value={search}
                                      onChange={(e) => setSearch(e.target.value)}
                                      placeholder="Поиск по имени / id / логину"
                                      style={{ minWidth: 260 }}
                                    />
                                  </div>
                                </div>
                                <div className="table-scroll">
                                  <table className="table">
                                    <thead>
                                      <tr>
                                        <th>ID клиента</th>
                                        <th>Название клиента</th>
                                        <th>Кол-во проектов</th>
                                        <th>Статус клиента</th>
                                        <th>Тариф</th>
                                        <th>Остаток</th>
                                        <th>Общий объём данных за период</th>
                                        <th>Действия</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {filteredExpandedClients.length === 0 && (
                                        <tr>
                                          <td colSpan={8} className="muted" style={{ padding: 16 }}>
                                            У агента пока нет клиентов по текущему фильтру.
                                          </td>
                                        </tr>
                                      )}
                                      {filteredExpandedClients.map((client) => {
                                        const blacklistTotal = (client.pendingBlacklistAdds ?? 0) + (client.pendingBlacklistDeletes ?? 0);
                                        const hasEvents = (client.pendingChanges ?? 0) > 0 || (client.pendingCreates ?? 0) > 0 || blacklistTotal > 0;
                                        const isDebt = client.remaining < 0;
                                        return (
                                          <tr
                                            key={client.id}
                                            className={`client-row${isDebt ? ' row--debt' : ''}`}
                                            style={{
                                              cursor: 'pointer',
                                              backgroundColor: selectedClientId === client.id ? '#f7f8fc' : undefined,
                                            }}
                                            onClick={() => setSelectedClientId(client.id)}
                                          >
                                            <td className="muted">{client.id}</td>
                                            <td>
                                              <div className="name">{client.name}</div>
                                              {hasEvents && (
                                                <div className="chip-stack">
                                                  {client.pendingChanges > 0 && (
                                                    <span className="badge badge--orange">Изменения: {client.pendingChanges}</span>
                                                  )}
                                                  {client.pendingCreates > 0 && (
                                                    <span className="badge badge--gray">Создания: {client.pendingCreates}</span>
                                                  )}
                                                </div>
                                              )}
                                            </td>
                                            <td>{client.projectCount}</td>
                                            <td>
                                              {client.projectCount === 0 ? (
                                                <span className="badge badge--info">Нет проектов</span>
                                              ) : (
                                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                                  <span
                                                    style={{
                                                      width: 10,
                                                      height: 10,
                                                      borderRadius: '50%',
                                                      backgroundColor: STATUS_COLORS[client.status],
                                                    }}
                                                  />
                                                  <span className="sub" style={{ whiteSpace: 'nowrap' }}>{client.status}</span>
                                                </span>
                                              )}
                                            </td>
                                            <td>{client.tariffAmount == null ? '-' : client.tariffAmount}</td>
                                            <td>
                                              <div className={isDebt ? 'remaining-negative' : undefined}>{client.remaining}</div>
                                              {isDebt && <div className="sub" style={{ color: '#d23' }}>долг</div>}
                                            </td>
                                            <td>{client.totalVolume}</td>
                                            <td>
                                              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                                <button
                                                  className="btn btn--secondary"
                                                  type="button"
                                                  onClick={(e) => {
                                                    e.stopPropagation();
                                                    setSelectedClientId(client.id);
                                                  }}
                                                >
                                                  Смотреть
                                                </button>
                                                <button
                                                  className="btn btn--primary"
                                                  type="button"
                                                  onClick={(e) => {
                                                    e.stopPropagation();
                                                    setCardClientId(client.id);
                                                    setCardClientData({
                                                      name: client.name,
                                                      inn: client.inn,
                                                      phone: client.phone,
                                                      contact: client.contact,
                                                      telegramNotificationsChatId: client.telegramNotificationsChatId,
                                                      telegramAutoPauseEnabled: client.telegramAutoPauseEnabled,
                                                      uniqueProjectNamesEnabled: client.uniqueProjectNamesEnabled,
                                                      login: client.login,
                                                    });
                                                  }}
                                                >
                                                  Карточка
                                                </button>
                                                <button
                                                  className="btn btn--secondary"
                                                  type="button"
                                                  onClick={(e) => {
                                                    e.stopPropagation();
                                                    void handleOpenClientCabinet(client.id);
                                                  }}
                                                  disabled={openingClientCabinetId === client.id}
                                                >
                                                  {openingClientCabinetId === client.id ? 'Переходим…' : 'Перейти в ЛК'}
                                                </button>
                                              </div>
                                            </td>
                                          </tr>
                                        );
                                      })}
                                    </tbody>
                                  </table>
                                </div>
                              </div>

                              {selectedClient ? (
                                <div className="table-card" style={{ display: 'grid', gap: 16 }}>
                                  <div className="client-summary">
                                    <div className="client-summary__header">
                                      <div>
                                        <div className="client-summary__title">{selectedClient.name}</div>
                                        <div className="client-summary__meta">
                                          <span className="sub">ID: {selectedClient.id}</span>
                                          <span className="sub">Логин: {selectedClient.login}</span>
                                          <span className={`badge ${selectedClient.remaining < 0 ? 'badge--orange' : 'badge--green'}`}>
                                            {selectedClient.remaining < 0 ? 'Есть долг' : 'В норме'}
                                          </span>
                                        </div>
                                      </div>
                                    </div>

                                    <div className="client-summary__actions-panel">
                                      <div className="client-summary__panel-grid">
                                        <section className="client-summary__section">
                                          <div className="client-summary__section-title">Навигация</div>
                                          <div className="client-summary__section-actions">
                                            {onOpenClientChanges && (
                                              <button type="button" className="btn btn--secondary" onClick={() => onOpenClientChanges(selectedClient.id, selectedClient.name)}>
                                                Изменения клиента
                                                {(selectedClient.pendingChanges > 0 || selectedClient.pendingCreates > 0) && (
                                                  <span className="btn__meta">
                                                    {selectedClient.pendingChanges > 0 && (
                                                      <span className="badge badge--orange">Изм: {selectedClient.pendingChanges}</span>
                                                    )}
                                                    {selectedClient.pendingCreates > 0 && (
                                                      <span className="badge badge--gray">Созд: {selectedClient.pendingCreates}</span>
                                                    )}
                                                  </span>
                                                )}
                                              </button>
                                            )}
                                            {onOpenClientBlacklistChanges && (
                                              <button type="button" className="btn btn--secondary" onClick={() => onOpenClientBlacklistChanges(selectedClient.id, selectedClient.name)}>
                                                События ЧС
                                                {selectedBlacklistTotal > 0 && (
                                                  <span className="btn__meta">
                                                    <span className="badge badge--orange">ЧС: {selectedBlacklistTotal}</span>
                                                  </span>
                                                )}
                                              </button>
                                            )}
                                            {onOpenClientProjects && (
                                              <button type="button" className="btn btn--secondary" onClick={() => onOpenClientProjects(selectedClient.id, selectedClient.name)}>
                                                Перейти к проектам
                                              </button>
                                            )}
                                          </div>
                                        </section>

                                        <section className="client-summary__section">
                                          <div className="client-summary__section-title">Финансы</div>
                                          <div className="client-summary__section-actions">
                                            {onOpenClientBalance && (
                                              <button type="button" className="btn btn--primary" onClick={() => onOpenClientBalance(selectedClient.id, selectedClient.name, 'tariff')}>
                                                Управление тарифами
                                              </button>
                                            )}
                                          </div>
                                        </section>

                                        <section className="client-summary__section">
                                          <div className="client-summary__section-title">Владелец</div>
                                          <div className="client-summary__section-actions" style={{ alignItems: 'stretch' }}>
                                            <div className="sub">
                                              Текущий владелец: <b>{formatOwnerLabel(selectedClient)}</b>
                                            </div>
                                            <select value={ownerTarget} onChange={(e) => setOwnerTarget(e.target.value)} style={{ minWidth: 240 }}>
                                              <option value="admin">Админ</option>
                                              {agents
                                                .filter((item) => !item.user.isDisabled || ownerTarget === `agent:${item.user.id}`)
                                                .map((item) => (
                                                  <option key={item.user.id} value={`agent:${item.user.id}`}>
                                                    {item.user.name || item.user.login} ({item.user.login})
                                                  </option>
                                                ))}
                                            </select>
                                            <button
                                              type="button"
                                              className="btn btn--secondary"
                                              disabled={
                                                (selectedClient.ownerType === 'admin' && ownerTarget === 'admin')
                                                || (selectedClient.ownerType === 'agent' && ownerTarget === `agent:${selectedClient.ownerUser?.id ?? 0}`)
                                              }
                                              onClick={() => {
                                                void handleTransferOwner();
                                              }}
                                            >
                                              Переназначить клиента
                                            </button>
                                          </div>
                                        </section>
                                      </div>
                                    </div>
                                  </div>

                                  <div className="summary-grid">
                                    <div className="summary-card">
                                      <div className="sub">Проектов</div>
                                      <div className="value">{selectedClient.projectCount}</div>
                                    </div>
                                    <div className="summary-card">
                                      <div className="sub">Объём за период</div>
                                      <div className="value">{selectedClient.totalVolume}</div>
                                    </div>
                                    <div className="summary-card">
                                      <div className="sub">Тариф</div>
                                      <div className="value">{selectedClient.tariffAmount == null ? '-' : selectedClient.tariffAmount}</div>
                                    </div>
                                    <div className="summary-card">
                                      <div className={`value${selectedClient.remaining < 0 ? ' value--negative' : ''}`}>Остаток: {selectedClient.remaining}</div>
                                      <div className="sub">Использовано: {selectedClient.usedTotal}</div>
                                      <div className="sub">Начислено: {selectedAccrued}</div>
                                    </div>
                                  </div>
                                </div>
                              ) : (
                                <div className="table-card" style={{ padding: 20 }}>
                                  <div style={{ fontSize: '1.05rem', fontWeight: 600 }}>Клиент не выбран</div>
                                  <div className="sub" style={{ marginTop: 8 }}>
                                    Выберите клиента в таблице выше, чтобы открыть detail-режим.
                                  </div>
                                </div>
                              )}

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
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {editModal && (
        <AgentEditModal
          mode={editModal.mode}
          agentId={editModal.agent?.user.id}
          initialName={editModal.agent?.user.name || editModal.agent?.user.login}
          initialLogin={editModal.agent?.user.login}
          initialDisabled={Boolean(editModal.agent?.user.isDisabled)}
          onClose={() => setEditModal(null)}
          onDone={() => {
            const keepExpandedAgentId = editModal.agent?.user.id ?? expandedAgentId;
            setEditModal(null);
            void loadData(keepExpandedAgentId, selectedClientId);
          }}
        />
      )}

      {cardClientId && cardClientData && (
        <AdminClientCardModal
          clientId={cardClientId}
          managerRole="admin"
          initialName={cardClientData.name}
          initialInn={cardClientData.inn || undefined}
          initialPhone={cardClientData.phone || undefined}
          initialContact={cardClientData.contact || undefined}
          initialTelegramNotificationsChatId={cardClientData.telegramNotificationsChatId || undefined}
          initialTelegramAutoPauseEnabled={cardClientData.telegramAutoPauseEnabled}
          initialUniqueProjectNamesEnabled={cardClientData.uniqueProjectNamesEnabled}
          initialLogin={cardClientData.login}
          onClose={() => setCardClientId(null)}
          onUpdated={() => {
            const keepExpandedAgentId = expandedAgentId;
            const keepSelectedClientId = cardClientId;
            setCardClientId(null);
            void loadData(keepExpandedAgentId, keepSelectedClientId);
          }}
        />
      )}
    </div>
  );
}

export default AdminAgentsScreen;
