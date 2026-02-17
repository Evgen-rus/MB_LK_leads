type Props = {
  from?: string | null;
  to?: string | null;
  className?: string;
};

const MONTHS_SHORT_RU = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'] as const;

type ParsedDateParts = {
  year: number;
  month: number;
  day: number;
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
    /^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::\d{2}(?:[.,]\d+)?)?(?:\s?(?:Z|[+-]\d{2}:?\d{2}))?)?$/;
  const slashLike =
    /^(\d{4})\/(\d{2})\/(\d{2})(?:[T\s](\d{2}):(\d{2})(?::\d{2}(?:[.,]\d+)?)?(?:\s?(?:Z|[+-]\d{2}:?\d{2}))?)?$/;

  const match = input.match(isoLike) ?? input.match(slashLike);
  if (!match) return null;

  const year = toInt(match[1]);
  const month = toInt(match[2]);
  const day = toInt(match[3]);

  if (year == null || month == null || day == null) return null;
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > 31) return null;

  return { year, month, day };
}

function cx(base: string, extra?: string): string {
  return extra ? `${base} ${extra}` : base;
}

function formatDateLabel(value: ParsedDateParts, includeYear: boolean): string {
  const monthLabel = MONTHS_SHORT_RU[value.month - 1];
  return includeYear ? `${value.day} ${monthLabel} ${value.year}` : `${value.day} ${monthLabel}`;
}

function buildRawLabel(from?: string | null, to?: string | null): string {
  const left = (from ?? '').trim();
  const right = (to ?? '').trim();
  if (left && right) return `${left} - ${right}`;
  return left || right;
}

function formatRange(from: ParsedDateParts, to: ParsedDateParts): string {
  const currentYear = new Date().getFullYear();

  if (from.year === to.year && from.month === to.month && from.day === to.day) {
    return formatDateLabel(from, from.year < currentYear);
  }

  if (from.year === to.year) {
    const includeYear = from.year < currentYear;
    const monthFrom = MONTHS_SHORT_RU[from.month - 1];
    const monthTo = MONTHS_SHORT_RU[to.month - 1];
    if (from.month === to.month) {
      return includeYear
        ? `${from.day}-${to.day} ${monthFrom} ${from.year}`
        : `${from.day}-${to.day} ${monthFrom}`;
    }
    return includeYear
      ? `${from.day} ${monthFrom} - ${to.day} ${monthTo} ${from.year}`
      : `${from.day} ${monthFrom} - ${to.day} ${monthTo}`;
  }

  return `${formatDateLabel(from, true)} - ${formatDateLabel(to, true)}`;
}

function DateRangeCompact({ from, to, className }: Props) {
  const rawLabel = buildRawLabel(from, to);
  if (!rawLabel) return null;

  const parsedFrom = from ? parseBackendDate(from) : null;
  const parsedTo = to ? parseBackendDate(to) : null;
  if (!parsedFrom || !parsedTo) {
    return (
      <span className={cx('date-range-compact', className)} title={rawLabel}>
        {rawLabel}
      </span>
    );
  }

  const label = formatRange(parsedFrom, parsedTo);
  return (
    <span className={cx('date-range-compact', className)} title={rawLabel}>
      {label}
    </span>
  );
}

export default DateRangeCompact;
