import { useEffect, useRef, useState, type CSSProperties } from 'react';

type DailyPoint = {
  date: string;
  value: number;
};

function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat('ru-RU').format(Number(value || 0));
}

function formatChartDate(value: string): string {
  const [year, month, day] = value.split('-');
  if (!year || !month || !day) return value;
  return `${day}.${month}.${year}`;
}

function DashboardDailyChart({ data }: { data: DailyPoint[] }) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [isVisible, setIsVisible] = useState(false);
  const max = Math.max(1, ...data.map((point) => point.value));

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;

    setIsVisible(false);

    if (typeof IntersectionObserver === 'undefined') {
      setIsVisible(true);
      return undefined;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0.25 },
    );

    observer.observe(root);
    return () => observer.disconnect();
  }, [data]);

  return (
    <div
      ref={rootRef}
      className={`dashboard-daily-chart${isVisible ? ' dashboard-daily-chart--visible' : ''}`}
      aria-label="Динамика полученных данных"
    >
      {data.map((point, index) => {
        const day = point.date.slice(8, 10);
        const height = point.value > 0 ? Math.max(8, (point.value / max) * 100) : 0;
        const barStyle = {
          '--bar-height': `${height}%`,
          '--bar-delay': `${index * 24}ms`,
        } as CSSProperties;
        return (
          <div
            className={`dashboard-daily-chart__bar${point.value <= 0 ? ' dashboard-daily-chart__bar--zero' : ''}`}
            key={point.date}
            style={barStyle}
            tabIndex={0}
            aria-label={`${formatChartDate(point.date)}: ${formatNumber(point.value)} получено данных`}
          >
            <span className="dashboard-daily-chart__fill" />
            <small>{day}</small>
            <span className="dashboard-daily-chart__tooltip" role="tooltip">
              <span>{formatChartDate(point.date)}</span>
              <strong>{formatNumber(point.value)}</strong>
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default DashboardDailyChart;
