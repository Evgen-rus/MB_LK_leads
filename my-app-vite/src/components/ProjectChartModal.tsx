import { useEffect, useMemo, useState } from 'react';
import {
  fetchAdminProjectChart,
  fetchProjectChart,
  type AdminDashboardBreakdownItem,
  type ProjectChart,
} from '../api';
import { formatProjectNameForDisplay, toDisplaySourceCode } from '../utils/sourceCodeDisplay';
import DashboardDailyChart from './DashboardDailyChart';

type ProjectChartModalProps = {
  projectId: number;
  projectName: string;
  fromDate: string;
  toDate: string;
  mode: 'client' | 'admin';
  onClose: () => void;
};

function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat('ru-RU').format(Number(value || 0));
}

function formatDate(value: string): string {
  const [year, month, day] = value.split('-');
  if (!year || !month || !day) return value;
  return `${day}.${month}.${year}`;
}

function SourceBreakdown({ items }: { items: AdminDashboardBreakdownItem[] }) {
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

function ProjectChartModal({
  projectId,
  projectName,
  fromDate,
  toDate,
  mode,
  onClose,
}: ProjectChartModalProps) {
  const [data, setData] = useState<ProjectChart | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const loader = mode === 'admin' ? fetchAdminProjectChart : fetchProjectChart;
    loader(projectId, { fromDate, toDate })
      .then((resp) => {
        if (!cancelled) setData(resp);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error && err.message
          ? err.message
          : 'Не удалось загрузить график проекта';
        setError(message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, fromDate, toDate, mode]);

  const periodLabel = useMemo(() => {
    const effectiveFrom = data?.fromDate || fromDate;
    const effectiveTo = data?.toDate || toDate;
    return `${formatDate(effectiveFrom)} - ${formatDate(effectiveTo)}`;
  }, [data?.fromDate, data?.toDate, fromDate, toDate]);

  const title = formatProjectNameForDisplay(data?.projectName || projectName);
  const total = data?.total ?? 0;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="modal-card project-chart-modal"
        style={{
          background: '#fff',
          borderRadius: 8,
          width: '100%',
          maxWidth: 760,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>
            {title}
          </div>
          <div className="sub" style={{ marginTop: 4 }}>
            Период: {periodLabel}
          </div>
        </div>

        <div style={{ padding: 20, display: 'grid', gap: 18 }}>
          {loading && <div className="dashboard-empty">Загрузка графика…</div>}
          {error && <div className="dashboard-error">{error}</div>}
          {!loading && !error && data && (
            <>
              <div className="project-chart-modal__metrics">
                <div className="dashboard-kpi">
                  <span>Всего</span>
                  <strong>{formatNumber(total)}</strong>
                </div>
                <div className="dashboard-kpi">
                  <span>Среднее за день</span>
                  <strong>{formatNumber(data.averageDaily)}</strong>
                </div>
                <div className="dashboard-kpi">
                  <span>Дней в периоде</span>
                  <strong>{formatNumber(data.leadsDaily.length)}</strong>
                </div>
              </div>

              {total <= 0 ? (
                <div className="dashboard-empty">За выбранный период данных по проекту нет.</div>
              ) : (
                <>
                  <section style={{ display: 'grid', gap: 10 }}>
                    <div className="section-title">Динамика по дням</div>
                    <DashboardDailyChart data={data.leadsDaily} />
                  </section>
                  <section style={{ display: 'grid', gap: 10 }}>
                    <div className="section-title">Разбивка по A/B/C/D</div>
                    <SourceBreakdown items={data.sourceBreakdown} />
                  </section>
                </>
              )}
            </>
          )}
        </div>

        <div
          style={{
            position: 'sticky',
            bottom: 0,
            background: '#fff',
            padding: '12px 20px',
            borderTop: '1px solid #eee',
            display: 'flex',
            justifyContent: 'flex-end',
          }}
        >
          <button type="button" className="btn" onClick={onClose}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}

export default ProjectChartModal;
