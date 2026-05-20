import { useEffect, useMemo, useState } from 'react';
import {
  fetchAdminDashboard,
  fetchAdminUsers,
  type AdminDashboard as AdminDashboardData,
  type AdminDashboardAttentionClient,
  type AdminDashboardBreakdownItem,
  type AdminDashboardRankingItem,
  type UserInfo,
} from '../api';
import {
  getSourceCodeFilterOptions,
  RAW_SOURCE_CODES,
  toDisplaySourceCode,
} from '../utils/sourceCodeDisplay';
import DateRangeFilter from './DateRangeFilter';
import DashboardDailyChart from './DashboardDailyChart';

type DashboardFilters = {
  period: 'yesterday' | 'today' | '7d' | '30d' | 'custom';
  fromDate: string;
  toDate: string;
  clientId: number | null;
  sources: string[];
  includeAgentClients: boolean;
};

type AdminDashboardProps = {
  onOpenClient?: (clientId: number, clientName: string) => void;
  onOpenProject?: (clientId: number, clientName: string) => void;
  onOpenLeads?: (fromDate: string, toDate: string) => void;
  onOpenActivity?: () => void;
};

const STORAGE_KEY = 'admin_dashboard_filters';

function formatDateInput(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function getRangeForPeriod(period: DashboardFilters['period']): Pick<DashboardFilters, 'fromDate' | 'toDate'> {
  const today = new Date();
  if (period === 'today') {
    const value = formatDateInput(today);
    return { fromDate: value, toDate: value };
  }
  if (period === '7d') {
    return { fromDate: formatDateInput(addDays(today, -6)), toDate: formatDateInput(today) };
  }
  if (period === '30d') {
    return { fromDate: formatDateInput(addDays(today, -29)), toDate: formatDateInput(today) };
  }
  const yesterday = addDays(today, -1);
  const value = formatDateInput(yesterday);
  return { fromDate: value, toDate: value };
}

function detectPeriodForRange(fromDate: string, toDate: string): DashboardFilters['period'] {
  const periods: Array<Exclude<DashboardFilters['period'], 'custom'>> = ['yesterday', 'today', '7d', '30d'];
  for (const period of periods) {
    const range = getRangeForPeriod(period);
    if (range.fromDate === fromDate && range.toDate === toDate) {
      return period;
    }
  }
  return 'custom';
}

function getDefaultFilters(): DashboardFilters {
  return {
    period: 'today',
    ...getRangeForPeriod('today'),
    clientId: null,
    sources: [...RAW_SOURCE_CODES],
    includeAgentClients: true,
  };
}

function readSavedFilters(): DashboardFilters {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return getDefaultFilters();
    const parsed = JSON.parse(raw) as Partial<DashboardFilters>;
    const fallback = getDefaultFilters();
    const period = parsed.period || fallback.period;
    const range = period === 'custom'
      ? {
          fromDate: parsed.fromDate || fallback.fromDate,
          toDate: parsed.toDate || fallback.toDate,
        }
      : getRangeForPeriod(period);
    const sources = Array.isArray(parsed.sources)
      ? parsed.sources.filter((source) => RAW_SOURCE_CODES.includes(source as (typeof RAW_SOURCE_CODES)[number]))
      : fallback.sources;
    return {
      period,
      ...range,
      clientId: typeof parsed.clientId === 'number' ? parsed.clientId : null,
      sources: sources.length ? sources : fallback.sources,
      includeAgentClients: parsed.includeAgentClients !== false,
    };
  } catch {
    return getDefaultFilters();
  }
}

function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat('ru-RU').format(Number(value || 0));
}

function riskLabel(level: AdminDashboardAttentionClient['level']): string {
  if (level === 'debt') return 'Долг';
  if (level === 'critical') return 'Критично';
  if (level === 'risk') return 'Риск';
  return 'Предупреждение';
}

function riskClass(level: AdminDashboardAttentionClient['level']): string {
  if (level === 'debt' || level === 'critical') return 'dashboard-risk dashboard-risk--critical';
  if (level === 'risk') return 'dashboard-risk dashboard-risk--risk';
  return 'dashboard-risk dashboard-risk--warning';
}

function MiniBarChart({ items }: { items: AdminDashboardBreakdownItem[] }) {
  const max = Math.max(1, ...items.map((item) => item.value));
  return (
    <div className="dashboard-bars">
      {items.map((item) => (
        <div className="dashboard-bars__row" key={item.key}>
          <span className="dashboard-bars__label">{toDisplaySourceCode(item.label)}</span>
          <div className="dashboard-bars__track">
            <span style={{ width: `${Math.max(3, (item.value / max) * 100)}%` }} />
          </div>
          <strong>{formatNumber(item.value)}</strong>
        </div>
      ))}
    </div>
  );
}

function RankingList({
  items,
  emptyText,
  onOpenClient,
}: {
  items: AdminDashboardRankingItem[];
  emptyText: string;
  onOpenClient?: (clientId: number, clientName: string) => void;
}) {
  if (!items.length) {
    return <div className="dashboard-empty">{emptyText}</div>;
  }
  return (
    <div className="dashboard-ranking">
      {items.map((item, index) => (
        <button
          type="button"
          key={`${item.clientId}-${index}`}
          className="dashboard-ranking__row"
          onClick={() => onOpenClient?.(item.clientId, item.clientName)}
        >
          <span className="dashboard-ranking__index">{index + 1}</span>
          <span className="dashboard-ranking__body">
            <strong>{item.clientName}</strong>
            <small>{item.ownerName ? `Агент: ${item.ownerName}` : `${item.activeProjects} активных проектов`}</small>
          </span>
          <strong className="dashboard-ranking__value">{formatNumber(item.value)}</strong>
        </button>
      ))}
    </div>
  );
}

function AdminDashboard({ onOpenClient, onOpenProject, onOpenLeads, onOpenActivity }: AdminDashboardProps) {
  const [filters, setFilters] = useState<DashboardFilters>(() => readSavedFilters());
  const [clients, setClients] = useState<UserInfo[]>([]);
  const [data, setData] = useState<AdminDashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAdminUsers({ includeAgents: true })
      .then((users) => setClients(users.filter((user) => user.role === 'client')))
      .catch((err) => console.error(err));
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
    } catch {
      /* ignore */
    }
  }, [filters]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const resp = await fetchAdminDashboard(filters);
        if (!cancelled) setData(resp);
      } catch (err) {
        console.error(err);
        if (!cancelled) setError(err instanceof Error ? err.message : 'Не удалось загрузить дашборд');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [filters]);

  const visibleClients = useMemo(() => (
    filters.includeAgentClients ? clients : clients.filter((client) => client.ownerAgentId == null)
  ), [clients, filters.includeAgentClients]);

  const attentionRows = useMemo(() => {
    if (!data) return [];
    return [
      ...data.attention.criticalClients,
      ...data.attention.riskClients,
      ...data.attention.warningClients,
    ].slice(0, 8);
  }, [data]);

  function updateDateRange(range: { from: string; to: string }) {
    setFilters((prev) => ({
      ...prev,
      period: detectPeriodForRange(range.from, range.to),
      fromDate: range.from,
      toDate: range.to,
    }));
  }

  function toggleSource(source: string) {
    setFilters((prev) => {
      const next = prev.sources.includes(source)
        ? prev.sources.filter((item) => item !== source)
        : [...prev.sources, source];
      return { ...prev, sources: next.length ? next : [...RAW_SOURCE_CODES] };
    });
  }

  const sourceOptions = getSourceCodeFilterOptions(RAW_SOURCE_CODES);

  return (
    <div className="admin-dashboard">
      <div className="admin-dashboard__toolbar table-card">
        <div className="filters admin-dashboard__filters">
          <DateRangeFilter
            from={filters.fromDate}
            to={filters.toDate}
            onChange={updateDateRange}
          />
          <select
            value={filters.clientId ?? ''}
            onChange={(e) => setFilters((prev) => ({ ...prev, clientId: e.target.value ? Number(e.target.value) : null }))}
          >
            <option value="">Все клиенты</option>
            {visibleClients.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name || client.login} (id: {client.id})
              </option>
            ))}
          </select>
          <label className="dashboard-toggle">
            <input
              type="checkbox"
              checked={filters.includeAgentClients}
              onChange={(e) => setFilters((prev) => ({ ...prev, includeAgentClients: e.target.checked, clientId: null }))}
            />
            Клиенты агентов
          </label>
        </div>
        <div className="admin-dashboard__sources">
          {sourceOptions.map((option) => (
            <button
              type="button"
              key={option.value}
              className={`dashboard-source${filters.sources.includes(option.value) ? ' dashboard-source--active' : ''}`}
              onClick={() => toggleSource(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="table-card dashboard-error">{error}</div>}
      {loading && !data && <div className="table-card dashboard-empty">Загрузка дашборда…</div>}

      {data && (
        <>
          <div className="dashboard-kpis">
            <div className="dashboard-kpi">
              <span>Клиенты</span>
              <strong>{formatNumber(data.summary.clients)}</strong>
              <small>{filters.includeAgentClients ? 'с агентскими' : 'только прямые'}</small>
            </div>
            <div className="dashboard-kpi">
              <span>Проекты</span>
              <strong>{formatNumber(data.summary.projects)}</strong>
              <small>{formatNumber(data.summary.activeProjects)} активных</small>
            </div>
            <div className="dashboard-kpi">
              <span>Данные за период</span>
              <strong>{formatNumber(data.summary.leadsPeriod)}</strong>
              <small>получено данных</small>
            </div>
            <div className="dashboard-kpi">
              <span>Данные сегодня</span>
              <strong>{formatNumber(data.summary.leadsToday)}</strong>
              <small>получено данных</small>
            </div>
            <div className="dashboard-kpi">
              <span>7 дней</span>
              <strong>{formatNumber(data.summary.leads7Days)}</strong>
              <small>получено данных</small>
            </div>
            <div className="dashboard-kpi">
              <span>30 дней</span>
              <strong>{formatNumber(data.summary.leads30Days)}</strong>
              <small>получено данных</small>
            </div>
            <div className="dashboard-kpi">
              <span>Остаток</span>
              <strong>{formatNumber(data.summary.totalRemaining)}</strong>
              <small>{formatNumber(data.attention.criticalClients.length)} критичных</small>
            </div>
          </div>

          <div className="dashboard-grid dashboard-grid--attention">
            <section className="table-card dashboard-section dashboard-section--attention">
              <div className="dashboard-section__header">
                <div>
                  <h2>Требует внимания</h2>
                  <p>Приоритет: критично, риск, предупреждение и операционные проблемы</p>
                </div>
                <button type="button" className="btn btn--ghost" onClick={onOpenActivity}>
                  Все события
                </button>
              </div>
              <div className="dashboard-alerts">
                <div className="dashboard-alert dashboard-alert--critical">
                  <span>Критично</span>
                  <strong>{formatNumber(data.attention.criticalClients.length)}</strong>
                  <small>ниже signal3 / долг</small>
                </div>
                <div className="dashboard-alert dashboard-alert--risk">
                  <span>Риск</span>
                  <strong>{formatNumber(data.attention.riskClients.length)}</strong>
                  <small>ниже signal2</small>
                </div>
                <div className="dashboard-alert dashboard-alert--warning">
                  <span>Предупреждение</span>
                  <strong>{formatNumber(data.attention.warningClients.length)}</strong>
                  <small>ниже signal1</small>
                </div>
                <button
                  type="button"
                  className="dashboard-alert dashboard-alert--blocked"
                  onClick={() => {
                    const first = data.attention.operatorBlockedProjects[0];
                    if (first?.clientId) onOpenProject?.(first.clientId, first.clientName || 'Клиент');
                  }}
                >
                  <span>Блокировки</span>
                  <strong>{formatNumber(data.summary.operatorBlockedProjects)}</strong>
                  <small>B4 оператор</small>
                </button>
              </div>

              <div className="dashboard-attention-table">
                <div className="dashboard-attention-table__head">
                  <span>Клиент</span>
                  <span>Уровень</span>
                  <span>Остаток</span>
                  <span>Тариф</span>
                  <span>Актив.</span>
                  <span>Переход</span>
                </div>
                {attentionRows.length ? attentionRows.map((item) => (
                  <div className="dashboard-attention-table__row" key={`${item.clientId}-${item.level}`}>
                    <span>
                      <strong>{item.clientName}</strong>
                      <small>{item.ownerName ? `Агент: ${item.ownerName}` : 'Владелец: Админ'}</small>
                    </span>
                    <span className={riskClass(item.level)}>{riskLabel(item.level)}</span>
                    <strong className={item.remaining <= 0 ? 'value--negative' : ''}>{formatNumber(item.remaining)}</strong>
                    <span>{item.tariffAmount != null ? formatNumber(item.tariffAmount) : '—'}</span>
                    <span>{formatNumber(item.activeProjects)}</span>
                    <button type="button" className="btn btn--secondary" onClick={() => onOpenClient?.(item.clientId, item.clientName)}>
                      Клиент
                    </button>
                  </div>
                )) : (
                  <div className="dashboard-empty">Клиентов в тарифном риске нет.</div>
                )}
              </div>
            </section>

            <aside className="table-card dashboard-section dashboard-section--side">
              <div className="dashboard-section__header">
                <div>
                  <h2>Проекты</h2>
                  <p>Статусы по текущей выборке</p>
                </div>
              </div>
              <div className="dashboard-status-list">
                {data.charts.projectStatuses.map((item) => (
                  <div className="dashboard-status-list__row" key={item.key}>
                    <span>{item.label}</span>
                    <strong>{formatNumber(item.value)}</strong>
                  </div>
                ))}
              </div>
              <button type="button" className="dashboard-link-row" onClick={() => onOpenLeads?.(filters.fromDate, filters.toDate)}>
                <span>Непривязанные лиды</span>
                <strong>{formatNumber(data.attention.unlinkedLeads.total)}</strong>
              </button>
              <button type="button" className="dashboard-link-row" onClick={onOpenActivity}>
                <span>Ошибки операций</span>
                <strong>{formatNumber(data.attention.operationErrors.total)}</strong>
              </button>
            </aside>
          </div>

          <div className="dashboard-grid dashboard-grid--charts">
            <section className="table-card dashboard-section dashboard-section--chart">
              <div className="dashboard-section__header">
                <div>
                  <h2>Динамика данных</h2>
                  <p>Данные по дням</p>
                </div>
              </div>
              <DashboardDailyChart data={data.charts.leadsDaily} />
            </section>

            <section className="table-card dashboard-section">
              <div className="dashboard-section__header">
                <div>
                  <h2>Источники</h2>
                  <p>Frontend A/B/C/D, backend B1/B2/B3/B4</p>
                </div>
              </div>
              <MiniBarChart items={data.charts.sourceBreakdown} />
            </section>

            <section className="table-card dashboard-section">
              <div className="dashboard-section__header">
                <div>
                  <h2>Топ клиентов</h2>
                  <p>По количеству полученных данных</p>
                </div>
              </div>
              <RankingList items={data.rankings.topClientsByLeads} emptyText="Нет данных за период." onOpenClient={onOpenClient} />
            </section>
          </div>

          <section className="table-card dashboard-section">
            <div className="dashboard-section__header">
              <div>
                <h2>Активные клиенты</h2>
                <p>Топ по количеству активных проектов</p>
              </div>
            </div>
            <RankingList items={data.rankings.topClientsByActiveProjects} emptyText="Активных проектов нет." onOpenClient={onOpenClient} />
          </section>
        </>
      )}
    </div>
  );
}

export default AdminDashboard;
