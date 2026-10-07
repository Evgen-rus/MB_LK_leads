import type { AnalysisPeriod } from "./types";

export type PeriodSplit = "week" | "month";

export const MAX_ANALYSIS_PERIODS = 64;

function parseDate(value: string): Date {
  const date = new Date(`${value}T00:00:00.000Z`);
  if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== value) {
    throw new RangeError("Укажите корректные даты периода.");
  }
  return date;
}

function toIsoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function splitAnalysisPeriod(period: AnalysisPeriod, split: PeriodSplit): AnalysisPeriod[] {
  const start = parseDate(period.period_start);
  const end = parseDate(period.period_end);
  if (start > end) throw new RangeError("Дата начала не может быть позже даты окончания.");

  const periods = [{ ...period }];
  const cursor = new Date(start);

  while (cursor <= end) {
    const boundary = split === "week"
      ? new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth(), cursor.getUTCDate() + (7 - cursor.getUTCDay()) % 7))
      : new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 0));
    const chunkEnd = boundary < end ? boundary : end;
    const chunk = { period_start: toIsoDate(cursor), period_end: toIsoDate(chunkEnd) };

    if (chunk.period_start !== period.period_start || chunk.period_end !== period.period_end) {
      periods.push(chunk);
      if (periods.length > MAX_ANALYSIS_PERIODS) {
        throw new RangeError(`Разрешено не более ${MAX_ANALYSIS_PERIODS} периодов вместе с общим.`);
      }
    }

    cursor.setTime(chunkEnd.getTime() + 86_400_000);
  }

  return periods;
}

export function buildHierarchicalAnalysisPeriods(period: AnalysisPeriod): AnalysisPeriod[] {
  const whole = splitAnalysisPeriod(period, "month")[0];
  const monthSlices = splitAnalysisPeriod(period, "month").slice(1);
  if (monthSlices.length === 0) monthSlices.push(whole);

  const periods = [whole];
  const seen = new Set([`${whole.period_start}:${whole.period_end}`]);
  for (const month of monthSlices) {
    const monthKey = `${month.period_start}:${month.period_end}`;
    if (!seen.has(monthKey)) {
      periods.push(month);
      seen.add(monthKey);
    }
    for (const week of splitAnalysisPeriod(month, "week").slice(1)) {
      const weekKey = `${week.period_start}:${week.period_end}`;
      if (!seen.has(weekKey)) {
        periods.push(week);
        seen.add(weekKey);
      }
    }
    if (periods.length > MAX_ANALYSIS_PERIODS) {
      throw new RangeError(`Иерархическая разбивка превышает лимит ${MAX_ANALYSIS_PERIODS} периодов. Уменьшите диапазон или отключите разбивку.`);
    }
  }
  return periods;
}

export function periodDayCount(period: AnalysisPeriod): number | null {
  try {
    const start = parseDate(period.period_start);
    const end = parseDate(period.period_end);
    if (start > end) return null;
    return Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
  } catch {
    return null;
  }
}

export function incompletePeriodLabel(period: AnalysisPeriod, split: PeriodSplit | null): string | null {
  if (!split) return null;
  try {
    const start = parseDate(period.period_start);
    const end = parseDate(period.period_end);
    if (start > end) return null;

    const complete = split === "week"
      ? start.getUTCDay() === 1 && end.getUTCDay() === 0
      : start.getUTCDate() === 1 && end.getUTCDate() === new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() + 1, 0)).getUTCDate();
    return complete ? null : split === "week" ? "неполная неделя" : "неполный месяц";
  } catch {
    return null;
  }
}
