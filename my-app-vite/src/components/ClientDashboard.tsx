import { useEffect, useState } from 'react';
import {
  fetchClientDashboard,
  type ActivityEvent,
  type ClientDashboard as ClientDashboardData,
  type ClientDashboardAttentionProject,
  type ClientDashboardProjectRankingItem,
} from '../api';
import {
  getSourceCodeFilterOptions,
  RAW_SOURCE_CODES,
  toDisplaySourceCode,
  formatProjectNameForDisplay,
} from '../utils/sourceCodeDisplay';
import DateRangeFilter from './DateRangeFilter';
import DashboardDailyChart from './DashboardDailyChart';
import DateTimeCompact from './DateTimeCompact';

type DashboardFilters = {
  fromDate: string;
  toDate: string;
  sources: string[];
};

type ClientDashboardProps = {
  onOpenLeads?: (fromDate: string, toDate: string) => void;
  onOpenProjects?: () => void;
  onOpenBalance?: () => void;
  onOpenActivity?: () => void;
};

const STORAGE_KEY = 'client_dashboard_filters';

function formatDateInput(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function getDefaultFilters(): DashboardFilters {
  const today = formatDateInput(new Date());
  return {
    fromDate: today,
    toDate: today,
    sources: [...RAW_SOURCE_CODES],
  };
}

function readSavedFilters(): DashboardFilters {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return getDefaultFilters();
    const parsed = JSON.parse(raw) as Partial<DashboardFilters>;
    const fallback = getDefaultFilters();
    const sources = Array.isArray(parsed.sources)
      ? parsed.sources.filter((source) => RAW_SOURCE_CODES.includes(source as (typeof RAW_SOURCE_CODES)[number]))
      : fallback.sources;
    return {
      fromDate: parsed.fromDate || fallback.fromDate,
      toDate: parsed.toDate || fallback.toDate,
      sources: sources.length ? sources : fallback.sources,
    };
  } catch {
    return getDefaultFilters();
  }
}

function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat('ru-RU').format(Number(value || 0));
}

function formatAverage(value: number | null | undefined): string {
  const numeric = Number(value || 0);
  if (numeric === 0) return '0';
  if (numeric < 1) return '< 1';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(numeric);
}

function forecastText(balance: ClientDashboardData['balance']): string {
  if (balance.estimatedDaysLeft === 0) return 'Остаток исчерпан';
  if (balance.estimatedDaysLeft == null) return 'Прогноз не рассчитан';
  return `примерно ${formatNumber(balance.estimatedDaysLeft)} дн.`;
}

function basisText(balance: ClientDashboardData['balance']): string {
  if (balance.averageDailySpendBasis === '7d') return 'по расходу за 7 дней';
  if (balance.averageDailySpendBasis === '30d') return 'по расходу за 30 дней';
  return 'нет расхода за 30 дней';
}

function AttentionProjects({
  items,
  onOpenProjects,
}: {
  items: ClientDashboardAttentionProject[];
  onOpenProjects?: () => void;
}) {
  if (!items.length) {
    return <div className="dashboard-empty">Проектов, требующих внимания, нет.</div>;
  }
  return (
    <div className="dashboard-ranking">
      {items.map((item) => (
        <button
          type="button"
          className="dashboard-ranking__row"
          key={`${item.projectId}-${item.reason}`}
          onClick={onOpenProjects}
        >
          <span className="dashboard-ranking__index">{toDisplaySourceCode(item.source)}</span>
          <span className="dashboard-ranking__body">
            <strong>{formatProjectNameForDisplay(item.projectName)}</strong>
            <small>{item.reasonLabel}</small>
          </span>
          <strong className="dashboard-ranking__value">{item.status}</strong>
        </button>
      ))}
    </div>
  );
}

function TopProjects({
  items,
  onOpenProjects,
}: {
  items: ClientDashboardProjectRankingItem[];
  onOpenProjects?: () => void;
}) {
  if (!items.length) {
    return <div className="dashboard-empty">Нет данных за выбранный период.</div>;
  }
  return (
    <div className="dashboard-ranking">
      {items.map((item, index) => (
        <button
          type="button"
          className="dashboard-ranking__row"
          key={item.projectId}
          onClick={onOpenProjects}
        >
          <span className="dashboard-ranking__index">{index + 1}</span>
          <span className="dashboard-ranking__body">
            <strong>{formatProjectNameForDisplay(item.projectName)}</strong>
            <small>{toDisplaySourceCode(item.source)} · {item.status}</small>
          </span>
          <strong className="dashboard-ranking__value">{formatNumber(item.value)}</strong>
        </button>
      ))}
    </div>
  );
}

function RecentEvents({ items, onOpenActivity }: { items: ActivityEvent[]; onOpenActivity?: () => void }) {
  if (!items.length) {
    return <div className="dashboard-empty">Событий пока нет.</div>;
  }
  return (
    <div className="dashboard-ranking">
      {items.map((item) => (
        <button type="button" className="dashboard-ranking__row" key={item.eventId} onClick={onOpenActivity}>
          <span className="dashboard-ranking__index">{item.entity.slice(0, 1).toUpperCase()}</span>
          <span className="dashboard-ranking__body">
            <strong>{item.description}</strong>
            <small>{item.projectName ? formatProjectNameForDisplay(item.projectName) : item.action}</small>
          </span>
          <span className="dashboard-ranking__value">
            <DateTimeCompact value={item.createdAt} />
          </span>
        </button>
      ))}
    </div>
  );
}

function ClientDashboard({ onOpenLeads, onOpenProjects, onOpenBalance, onOpenActivity }: ClientDashboardProps) {
  const [filters, setFilters] = useState<DashboardFilters>(() => readSavedFilters());
  const [data, setData] = useState<ClientDashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        const resp = await fetchClientDashboard(filters);
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
            onChange={(range) => setFilters((prev) => ({ ...prev, fromDate: range.from, toDate: range.to }))}
          />
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
              <strong className={data.summary.remaining <= 0 ? 'value--negative' : undefined}>
                {formatNumber(data.summary.remaining)}
              </strong>
              <small>{forecastText(data.balance)}</small>
            </div>
            <div className="dashboard-kpi">
              <span>Активные проекты</span>
              <strong>{formatNumber(data.summary.activeProjects)}</strong>
              <small>{formatNumber(data.summary.pausedProjects)} на паузе</small>
            </div>
          </div>

          <div className="dashboard-grid dashboard-grid--charts">
            <section className="table-card dashboard-section dashboard-section--chart">
              <div className="dashboard-section__header">
                <div>
                  <h2>Динамика данных</h2>
                  <p>Данные по дням</p>
                </div>
                <button type="button" className="btn btn--ghost" onClick={() => onOpenLeads?.(filters.fromDate, filters.toDate)}>
                  Идентификации
                </button>
              </div>
              <DashboardDailyChart data={data.charts.leadsDaily} />
            </section>

            <section className="table-card dashboard-section">
              <div className="dashboard-section__header">
                <div>
                  <h2>Остаток</h2>
                  <p>{basisText(data.balance)}</p>
                </div>
                <button type="button" className="btn btn--ghost" onClick={onOpenBalance}>
                  Баланс
                </button>
              </div>
              <div className="dashboard-alerts dashboard-alerts--compact">
                <div className="dashboard-alert dashboard-alert--blocked">
                  <span>Средний расход</span>
                  <strong>{formatAverage(data.balance.averageDailySpend)}</strong>
                  <small>в день</small>
                </div>
                <div className="dashboard-alert dashboard-alert--warning">
                  <span>Прогноз</span>
                  <strong>{forecastText(data.balance)}</strong>
                  <small>при текущем темпе</small>
                </div>
              </div>
            </section>

            <section className="table-card dashboard-section">
              <div className="dashboard-section__header">
                <div>
                  <h2>Статусы проектов</h2>
                  <p>По текущей выборке источников</p>
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
            </section>
          </div>

          <div className="dashboard-grid dashboard-grid--charts">
            <section className="table-card dashboard-section">
              <div className="dashboard-section__header">
                <div>
                  <h2>Проекты требуют внимания</h2>
                  <p>Пауза, блокировка или нет данных за 7 дней</p>
                </div>
                <button type="button" className="btn btn--ghost" onClick={onOpenProjects}>
                  Проекты
                </button>
              </div>
              <AttentionProjects items={data.attention.projects} onOpenProjects={onOpenProjects} />
            </section>

            <section className="table-card dashboard-section">
              <div className="dashboard-section__header">
                <div>
                  <h2>Топ проектов</h2>
                  <p>По полученным данным за период</p>
                </div>
              </div>
              <TopProjects items={data.rankings.topProjectsByLeads} onOpenProjects={onOpenProjects} />
            </section>

            <section className="table-card dashboard-section">
              <div className="dashboard-section__header">
                <div>
                  <h2>Последние события</h2>
                  <p>История действий в ЛК</p>
                </div>
                <button type="button" className="btn btn--ghost" onClick={onOpenActivity}>
                  История
                </button>
              </div>
              <RecentEvents items={data.recentEvents} onOpenActivity={onOpenActivity} />
            </section>
          </div>
        </>
      )}
    </div>
  );
}

export default ClientDashboard;
