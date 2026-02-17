type Props = {
  value?: string | null;
  className?: string;
};

const MONTHS_SHORT_RU = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'] as const;

type ParsedDateParts = {
  year: number;
  month: number;
  day: number;
  hours?: number;
  minutes?: number;
};

function toInt(value: string | undefined): number | null {
  if (!value) return null;
  const n = Number(value);
  return Number.isInteger(n) ? n : null;
}

function parseBackendDate(raw: string): ParsedDateParts | null {
  const input = raw.trim();
  if (!input) return null;

  const isoLike =
    /^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::\d{2}(?:[.,]\d+)?)?(?:\s?(?:Z|[+\-]\d{2}:?\d{2}))?)?$/;
  const slashLike =
    /^(\d{4})\/(\d{2})\/(\d{2})(?:[T\s](\d{2}):(\d{2})(?::\d{2}(?:[.,]\d+)?)?(?:\s?(?:Z|[+\-]\d{2}:?\d{2}))?)?$/;

  const match = input.match(isoLike) ?? input.match(slashLike);
  if (!match) return null;

  const year = toInt(match[1]);
  const month = toInt(match[2]);
  const day = toInt(match[3]);
  const hours = toInt(match[4] ?? undefined);
  const minutes = toInt(match[5] ?? undefined);

  if (year == null || month == null || day == null) return null;
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > 31) return null;
  if (hours != null && (hours < 0 || hours > 23)) return null;
  if (minutes != null && (minutes < 0 || minutes > 59)) return null;

  return { year, month, day, hours: hours ?? undefined, minutes: minutes ?? undefined };
}

function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

function cx(base: string, extra?: string): string {
  return extra ? `${base} ${extra}` : base;
}

function DateTimeCompact({ value, className }: Props) {
  if (value == null) return null;

  const parsed = parseBackendDate(value);
  if (!parsed) {
    return (
      <span className={cx('datetime-compact datetime-compact--raw', className)} title={value}>
        {value}
      </span>
    );
  }

  const currentYear = new Date().getFullYear();
  const monthLabel = MONTHS_SHORT_RU[parsed.month - 1];
  const dateLabel =
    parsed.year < currentYear ? `${parsed.day} ${monthLabel} ${parsed.year}` : `${parsed.day} ${monthLabel}`;
  const hasTime = parsed.hours != null && parsed.minutes != null;
  const timeLabel = hasTime ? `${pad2(parsed.hours!)}:${pad2(parsed.minutes!)}` : null;

  return (
    <span className={cx('datetime-compact', className)} title={value}>
      <span className="datetime-compact__date">{dateLabel}</span>
      {timeLabel && <span className="datetime-compact__time">{timeLabel}</span>}
    </span>
  );
}

export default DateTimeCompact;
