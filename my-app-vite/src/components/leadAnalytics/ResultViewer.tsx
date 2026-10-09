import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { fetchExportResult, fetchExportResultRows } from "./api";
import type {
  AnalyticsRow,
  SavedExportResult,
  SavedResultFilter,
  SavedResultPeriod,
  SavedResultRows,
  SavedResultTable,
} from "./types";

type ResultTab = "total" | "domains" | "sources" | "channels" | "dynamics" | "statuses" | "data";
type SortState = { key: string; direction: "desc" | "asc" } | null;
type FilterDraft = { contains: string; selected: string[]; min: string; max: string };
type Slice = { id: string; start: string; end: string; label: string; short: string; periodId?: string };

const PAGE_SIZE = 100;
const MAIN_METRICS = [
  "Всего идентификаций", "Качественные", "Кач. %", "Рабочий потенциал",
  "Сигнал спроса", "Сигнал спроса %", "Недозвон", "Недозвон %",
];
const EXTRA_METRICS = [
  "Рабочий потенциал %", "Уже наши / уже купил", "Уже наши / купил %", "Конкурент", "Конкурент %",
  "Не обработано", "Не обработано %", "Некачественные", "Некач. %", "Не подходит по гео",
  "Не подходит по гео %", "Требует проверки", "Требует проверки %", "Не учитывать",
];
const SHORT_LABELS: Record<string, string> = {
  "Всего идентификаций": "Всего",
  "Качественные": "Кач.",
  "Кач. %": "Кач.%",
  "Рабочий потенциал": "Потенц.",
  "Рабочий потенциал %": "Пот.%",
  "Сигнал спроса": "Спрос",
  "Сигнал спроса %": "Спрос %",
  "Недозвон": "Недозв.",
  "Недозвон %": "Недозв.%",
  "Уже наши / уже купил": "Купил",
  "Уже наши / купил %": "Купил %",
  "Конкурент": "Конкур.",
  "Конкурент %": "Конк.%",
  "Не обработано": "Не обр.",
  "Не обработано %": "Не обр.%",
  "Некачественные": "Некач.",
  "Некач. %": "Некач.%",
  "Не подходит по гео": "Гео",
  "Не подходит по гео %": "Гео %",
  "Требует проверки": "Проверка",
  "Требует проверки %": "Проверка %",
  "Полный источник": "Источник",
};
const TAB_LABELS: { id: ResultTab; label: string }[] = [
  { id: "total", label: "Итог" },
  { id: "domains", label: "Домены" },
  { id: "sources", label: "Источники" },
  { id: "channels", label: "Каналы" },
  { id: "dynamics", label: "Динамика" },
  { id: "statuses", label: "Статусы" },
  { id: "data", label: "Данные" },
];
const GENERAL_TABS = new Set<ResultTab>(["statuses", "data"]);

function parseDate(value: string): Date {
  return new Date(`${value}T00:00:00.000Z`);
}

function isoDate(value: Date): string {
  return value.toISOString().slice(0, 10);
}

function dateSlices(startValue: string, endValue: string): Slice[] {
  const start = parseDate(startValue);
  const end = parseDate(endValue);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) return [];
  const slices: Slice[] = [];
  let cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
  while (cursor <= end) {
    const monthStart = new Date(cursor);
    const monthEnd = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 0));
    const clippedStart = monthStart < start ? start : monthStart;
    const clippedEnd = monthEnd > end ? end : monthEnd;
    const date = isoDate(clippedStart);
    const month = new Intl.DateTimeFormat("ru-RU", { month: "long", timeZone: "UTC" }).format(clippedStart);
    const label = month.charAt(0).toLocaleUpperCase("ru-RU") + month.slice(1);
    slices.push({ id: date.slice(0, 7), start: date, end: isoDate(clippedEnd), label, short: label });
    cursor = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 1));
  }
  const years = new Set(slices.map((slice) => slice.start.slice(0, 4)));
  return years.size > 1 ? slices.map((slice) => ({ ...slice, short: `${slice.label} ${slice.start.slice(0, 4)}` })) : slices;
}

function weekSlices(month: Slice): Slice[] {
  const end = parseDate(month.end);
  let cursor = parseDate(month.start);
  const weeks: Slice[] = [];
  let index = 1;
  while (cursor <= end) {
    const endOffset = (7 - cursor.getUTCDay()) % 7;
    const weekEnd = new Date(cursor);
    weekEnd.setUTCDate(weekEnd.getUTCDate() + endOffset);
    const clippedEnd = weekEnd > end ? end : weekEnd;
    const start = isoDate(cursor);
    const finish = isoDate(clippedEnd);
    weeks.push({ id: `${month.id}-w${index++}`, start, end: finish, short: `${formatShortDate(start)}–${formatShortDate(finish)}`, label: `${formatDate(start)} — ${formatDate(finish)}` });
    cursor = new Date(clippedEnd);
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return weeks;
}

function formatDate(value: string): string {
  const date = parseDate(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("ru-RU", { timeZone: "UTC" }).format(date);
}

function formatShortDate(value: string): string {
  const date = parseDate(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", timeZone: "UTC" }).format(date);
}

function isPercent(key: string): boolean {
  return key.trim().endsWith("%");
}

function isTextFilter(filter: SavedResultFilter): filter is { contains?: string; selected?: string[] } {
  return "contains" in filter || "selected" in filter;
}

function formatValue(value: unknown, key: string): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
  const amount = isPercent(key) ? value * 100 : value;
  if (isPercent(key)) return `${new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)}%`;
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(amount);
}

function groupFor(key: string): string {
  if (["Домен", "Полный источник", "Канал", "Период", "Проект", "Группа статуса", "Исходный статус", "Статус"].includes(key)) return "Разрез";
  if (["Всего идентификаций", "Количество"].includes(key)) return "Объём";
  if (["Качественные", "Кач. %", "Рабочий потенциал", "Рабочий потенциал %"].includes(key)) return "Качество";
  if (["Уже наши / уже купил", "Уже наши / купил %", "Сигнал спроса", "Сигнал спроса %"].includes(key)) return "Спрос";
  if (["Недозвон", "Недозвон %"].includes(key)) return "Дозвон";
  if (["Не обработано", "Не обработано %", "Конкурент", "Конкурент %", "Некачественные", "Некач. %", "Не подходит по гео", "Не подходит по гео %", "Требует проверки", "Требует проверки %", "Не учитывать"].includes(key)) return "Проверка";
  return "Данные";
}

function compactLabel(key: string): string {
  return SHORT_LABELS[key] ?? key;
}

function filterDescription(key: string, filter: SavedResultFilter): string {
  if (isTextFilter(filter)) {
    const parts = [];
    if (filter.contains) parts.push(`содержит «${filter.contains}»`);
    if (filter.selected?.length) parts.push(filter.selected.join(", "));
    return parts.join(" · ");
  }
  const parts = [];
  const suffix = isPercent(key) ? "%" : "";
  if (filter.min !== undefined) parts.push(`от ${filter.min}${suffix}`);
  if (filter.max !== undefined) parts.push(`до ${filter.max}${suffix}`);
  return parts.join(" · ");
}

function fillColors(hex?: string): { backgroundColor?: string; color?: string } {
  if (!hex) return {};
  const backgroundColor = `#${hex.replace(/^#/, "")}`;
  const color = ({
    C6EFCE: "#185e43", FFEB9C: "#785900", FFC7CE: "#94323b", D9EAD3: "#4c6650", BDD7EE: "#375d7d",
  } as Record<string, string>)[hex.replace(/^#/, "").toUpperCase()];
  return { backgroundColor, ...(color ? { color } : {}) };
}

function rowsForTab(result: SavedExportResult, tab: ResultTab, period: SavedResultPeriod | null): AnalyticsRow[] {
  if (!period) return [];
  if (tab === "total") return [period.metrics];
  const kind = tab === "domains" ? "domain_channel" : tab === "sources" ? "source_channel" : tab === "channels" ? "channel" : null;
  return kind ? result.breakdowns[period.id]?.[kind] ?? [] : [];
}

function MetricIcon() {
  return <svg aria-hidden="true" viewBox="0 0 16 16" className="resultFilterIcon"><path d="M2.5 3h11L9.2 7.6v4.1l-2.4 1.3V7.6L2.5 3Z" /></svg>;
}

export function ResultViewer({
  groupName,
  groupId,
  exportId,
  onBack,
  onNewAnalysis,
  onDownload,
  canDownload,
  canStartNew,
}: {
  groupName: string;
  groupId: number;
  exportId: number;
  onBack: () => void;
  onNewAnalysis: () => void;
  onDownload: () => void;
  canDownload: boolean;
  canStartNew: boolean;
}) {
  const [result, setResult] = useState<SavedExportResult | null>(null);
  const [resultLoading, setResultLoading] = useState(true);
  const [resultError, setResultError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [tab, setTab] = useState<ResultTab>("total");
  const [view, setView] = useState<"main" | "all">("main");
  const [monthId, setMonthId] = useState("all");
  const [sliceId, setSliceId] = useState("all");
  const [multiWeek, setMultiWeek] = useState(false);
  const [selectedWeeks, setSelectedWeeks] = useState<Slice[]>([]);
  const [selectionResult, setSelectionResult] = useState<SavedExportResult | null>(null);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const selectionIds = useMemo(() => multiWeek ? selectedWeeks.map((week) => week.periodId!) : [], [multiWeek, selectedWeeks]);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Record<string, SavedResultFilter>>({});
  const [sort, setSort] = useState<SortState>(null);
  const [page, setPage] = useState(1);
  const [tableResult, setTableResult] = useState<SavedResultRows | null>(null);
  const [tableLoading, setTableLoading] = useState(false);
  const [tableError, setTableError] = useState("");
  const [filterKey, setFilterKey] = useState<string | null>(null);
  const [filterRect, setFilterRect] = useState<DOMRect | null>(null);
  const [filterSearch, setFilterSearch] = useState("");
  const [filterDraft, setFilterDraft] = useState<FilterDraft>({ contains: "", selected: [], min: "", max: "" });
  const [filterError, setFilterError] = useState("");
  const tableRequest = useRef(0);

  useEffect(() => {
    let active = true;
    setResultLoading(true);
    setResultError("");
    setResult(null);
    fetchExportResult(groupId, exportId)
      .then((data) => { if (active) setResult(data); })
      .catch((error: unknown) => { if (active) setResultError(error instanceof Error ? error.message : "Не удалось открыть сохранённую аналитику"); })
      .finally(() => { if (active) setResultLoading(false); });
    return () => { active = false; };
  }, [groupId, exportId, refresh]);

  useEffect(() => {
    setMultiWeek(false);
    setSelectedWeeks([]);
    setMonthId("all");
    setSliceId("all");
  }, [groupId, exportId]);

  useEffect(() => {
    let active = true;
    setSelectionResult(null);
    setSelectionError("");
    setSelectionLoading(selectionIds.length > 0);
    if (!selectionIds.length) return;
    const timer = window.setTimeout(() => {
      fetchExportResult(groupId, exportId, selectionIds)
        .then((data) => { if (active) setSelectionResult(data); })
        .catch((error: unknown) => { if (active) setSelectionError(error instanceof Error ? error.message : "Не удалось объединить недели"); })
        .finally(() => { if (active) setSelectionLoading(false); });
    }, 160);
    return () => { active = false; window.clearTimeout(timer); };
  }, [groupId, exportId, selectionIds, refresh]);

  useEffect(() => {
    if (!result || (multiWeek && !selectionIds.length) || (tab !== "data" && tab !== "statuses")) {
      setTableResult(null);
      setTableLoading(false);
      setTableError("");
      return;
    }
    const requestId = ++tableRequest.current;
    let active = true;
    setTableResult(null);
    setTableLoading(true);
    setTableError("");
    const timer = window.setTimeout(() => {
      fetchExportResultRows(groupId, exportId, {
        table: tab as SavedResultTable,
        page,
        pageSize: PAGE_SIZE,
        query,
        filters,
        sort: sort?.key ?? null,
        direction: sort?.direction,
        periodIds: selectionIds,
      })
        .then((data) => {
          if (active && requestId === tableRequest.current) setTableResult(data);
        })
        .catch((error: unknown) => {
          if (active && requestId === tableRequest.current) setTableError(error instanceof Error ? error.message : "Не удалось загрузить строки отчёта");
        })
        .finally(() => {
          if (active && requestId === tableRequest.current) setTableLoading(false);
        });
    }, 160);
    return () => { active = false; window.clearTimeout(timer); };
  }, [result, groupId, exportId, tab, page, query, filters, sort, multiWeek, selectionIds]);

  useEffect(() => {
    if (!filterKey) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setFilterKey(null); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [filterKey]);

  const months = useMemo(() => result ? dateSlices(result.period_start, result.period_end) : [], [result]);
  const overall = useMemo(() => result?.periods.find((item) => item.period_start === result.period_start && item.period_end === result.period_end) ?? null, [result]);
  const selectedMonth = monthId === "all" ? null : months.find((month) => month.id === monthId) ?? null;
  const weeks = useMemo(() => selectedMonth ? weekSlices(selectedMonth) : [], [selectedMonth]);
  const findPeriod = (slice: Pick<Slice, "start" | "end"> & Partial<Pick<Slice, "periodId">>) =>
    result?.periods.find((item) => item.id === slice.periodId || item.period_start === slice.start && item.period_end === slice.end) ?? null;
  const activePeriod = multiWeek ? selectionResult?.periods[0] ?? null : tab === "data" || tab === "statuses"
    ? overall
    : monthId === "all" ? overall
      : sliceId === "all" ? (selectedMonth ? findPeriod(selectedMonth) : null)
        : findPeriod(weeks.find((week) => week.id === sliceId) ?? { start: "", end: "" });
  const firstSavedPeriod = result?.periods[0] ?? null;
  const firstSavedPeriodLabel = firstSavedPeriod ? `${formatDate(firstSavedPeriod.period_start)} — ${formatDate(firstSavedPeriod.period_end)}` : "Период не сохранён";
  const selectionLabel = selectedWeeks.length
    ? `Выбрано недель: ${selectedWeeks.length} · ${[...selectedWeeks].sort((a, b) => a.start.localeCompare(b.start)).map((week) => week.label).join("; ")}`
    : "Отметьте недели одного или нескольких месяцев";
  const activePeriodLabel = multiWeek ? selectionLabel : tab === "data" || tab === "statuses"
    ? overall ? `Общий срез · ${formatDate(overall.period_start)} — ${formatDate(overall.period_end)}` : `Общий срез не сохранён · первый срез ${firstSavedPeriodLabel}`
    : monthId === "all" ? overall ? `Весь период · ${formatDate(overall.period_start)} — ${formatDate(overall.period_end)}` : "Общий срез не сохранён"
        : sliceId === "all" ? selectedMonth?.label ?? "Период"
          : weeks.find((week) => week.id === sliceId)?.label ?? selectedMonth?.label ?? "Период";
  const displayedResult = multiWeek ? selectionResult : result;
  const mainRows = useMemo(() => displayedResult ? rowsForTab(displayedResult, tab, activePeriod) : [], [displayedResult, tab, activePeriod]);
  const dataTable = tab === "data" || tab === "statuses";
  const rawColumns = dataTable ? tableResult?.columns ?? [] : tab === "dynamics" ? [] : [...new Set(mainRows.flatMap((row) => Object.keys(row).filter((key) => key !== "_fills")))];
  const dimColumns = tab === "sources" || tab === "domains" ? (tab === "sources" ? ["Полный источник", "Канал"] : ["Домен", "Канал"])
    : tab === "channels" ? ["Канал"] : tab === "dynamics" ? ["Период"] : [];
  const hiddenMeta = new Set(["Период", "Проект"]);
  const metricColumns = rawColumns.filter((key) => !dimColumns.includes(key) && !hiddenMeta.has(key));
  const preferred = view === "main" ? MAIN_METRICS : [...MAIN_METRICS, ...EXTRA_METRICS];
  const projectedColumns = dataTable ? rawColumns : tab === "dynamics" ? [] : [
    ...dimColumns.filter((key) => rawColumns.includes(key)),
    ...preferred.filter((key) => metricColumns.includes(key)),
    ...(view === "all" ? metricColumns.filter((key) => !preferred.includes(key)) : []),
  ];

  const isGeneral = !multiWeek && GENERAL_TABS.has(tab);
  const kpiPeriod = isGeneral ? overall : activePeriod;
  const metrics = kpiPeriod?.metrics;
  const kpis = [
    { key: "Всего идентификаций", label: "Всего идентификаций", hint: "Идентификации в срезе", color: "" },
    { key: "Качественные", label: "Качественные", hintKey: "Кач. %", color: "good" },
    { key: "Рабочий потенциал", label: "Рабочий потенциал", hintKey: "Рабочий потенциал %", color: "good" },
    { key: "Сигнал спроса", label: "Сигнал спроса", hintKey: "Сигнал спроса %", color: "good" },
    { key: "Недозвон", label: "Недозвон", hintKey: "Недозвон %", color: "bad" },
  ];

  const dynamicsRows = useMemo(() => {
    if (!result) return [];
    const slices = multiWeek ? [...selectedWeeks].sort((a, b) => a.start.localeCompare(b.start)) : selectedMonth ? weeks : months;
    return slices.map((slice) => {
      const period = result.periods.find((item) => item.id === slice.periodId || item.period_start === slice.start && item.period_end === slice.end) ?? null;
      return {
        ...(period?.metrics ?? {}),
        Период: slice.short,
        _fills: period?.metrics._fills ?? {},
        _unavailable: !period || !Object.keys(period.metrics).some((key) => key !== "_fills"),
      } as AnalyticsRow;
    });
  }, [result, selectedMonth, weeks, months, multiWeek, selectedWeeks]);
  const dynamicsMetricColumns = [...new Set(dynamicsRows.flatMap((row) => Object.keys(row).filter((key) => key !== "_fills" && key !== "_unavailable" && !hiddenMeta.has(key))))];
  const dynamicsColumns = ["Период", ...(view === "main" ? MAIN_METRICS : [...MAIN_METRICS, ...EXTRA_METRICS]).filter((key) => dynamicsMetricColumns.includes(key)), ...(view === "all" ? dynamicsMetricColumns.filter((key) => !MAIN_METRICS.includes(key) && !EXTRA_METRICS.includes(key)) : [])];
  const queryColumns = tab === "dynamics" ? dynamicsColumns : projectedColumns;

  const localRows = useMemo(() => {
    if (dataTable) return [];
    const lowerQuery = query.trim().toLocaleLowerCase("ru-RU");
    const percentageQuery = lowerQuery.includes("%") ? lowerQuery.replace(/%/g, "") : "";
    const sourceRows = tab === "dynamics" ? dynamicsRows : mainRows;
    let rows = sourceRows.filter((row) => {
      const accepted = Object.entries(filters).every(([key, filter]) => {
        const value = row[key];
        if (isTextFilter(filter)) {
          const text = value === null || value === undefined ? "" : String(value);
          return (!filter.contains || text.toLocaleLowerCase("ru-RU").includes(filter.contains.toLocaleLowerCase("ru-RU"))) &&
            (!filter.selected?.length || filter.selected.some((selected) => selected.toLocaleLowerCase("ru-RU") === text.toLocaleLowerCase("ru-RU")));
        }
        const number = Number(value);
        if (!Number.isFinite(number)) return false;
        const comparable = isPercent(key) ? number * 100 : number;
        return (filter.min === undefined || comparable >= filter.min) && (filter.max === undefined || comparable <= filter.max);
      });
      if (!accepted) return false;
      if (!lowerQuery) return true;
      return Object.entries(row).some(([key, value]) => {
        if (!queryColumns.includes(key) || key === "_fills" || key === "_unavailable") return false;
        const displayed = `${String(value ?? "")} ${formatValue(value, key)}`.toLocaleLowerCase("ru-RU");
        return displayed.includes(lowerQuery) || Boolean(percentageQuery && isPercent(key) && displayed.replace(/%/g, "").includes(percentageQuery));
      });
    });
    if (sort) {
      rows = [...rows].sort((left, right) => {
        const a = left[sort.key];
        const b = right[sort.key];
        const order = typeof a === "number" && typeof b === "number"
          ? a - b
          : String(a ?? "").localeCompare(String(b ?? ""), "ru-RU", { numeric: true, sensitivity: "base" });
        return sort.direction === "desc" ? -order : order;
      });
    }
    return rows;
  }, [dataTable, tab, query, filters, sort, mainRows, dynamicsRows, queryColumns]);

  const localTotal = localRows.length;
  const pageCount = Math.max(1, Math.ceil((dataTable ? tableResult?.total ?? 0 : localTotal) / PAGE_SIZE));
  const visibleRows = dataTable
    ? tableResult?.rows ?? []
    : localRows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const columns = tab === "dynamics" ? dynamicsColumns : projectedColumns;
  const stickyCount = dataTable ? 0 : tab === "sources" || tab === "domains" ? 2 : tab === "channels" || tab === "dynamics" ? 1 : 0;
  const isTextColumn = (key: string): boolean => {
    if (isPercent(key)) return false;
    if (dataTable) {
      if (tableResult?.values && Object.prototype.hasOwnProperty.call(tableResult.values, key)) return true;
      const sample = tableResult?.rows.find((row) => row[key] !== null && row[key] !== undefined);
      if (typeof sample?.[key] === "number") return false;
    } else {
      const sampleRows = tab === "dynamics" ? dynamicsRows : mainRows;
      const sample = sampleRows.find((row) => row[key] !== null && row[key] !== undefined);
      if (typeof sample?.[key] === "number") return false;
    }
    return ![...MAIN_METRICS, ...EXTRA_METRICS, "Количество"].includes(key);
  };
  const availableValues = (key: string): string[] => {
    if (!isTextColumn(key)) return [];
    if (dataTable) return tableResult?.values?.[key] ?? [];
    const rows = tab === "dynamics" ? dynamicsRows : mainRows;
    return [...new Set(rows.map((row) => String(row[key] ?? "")))].sort((a, b) => a.localeCompare(b, "ru-RU", { numeric: true }));
  };

  function resetTableState() {
    setQuery("");
    setFilters({});
    setSort(null);
    setPage(1);
  }

  function resetFiltersAndQuery() {
    setQuery("");
    setFilters({});
    setPage(1);
  }

  function selectMonth(month: Slice | null) {
    if (!month) {
      setMultiWeek(false);
      setSelectedWeeks([]);
    }
    setMonthId(month?.id ?? "all");
    setSliceId("all");
    setPage(1);
    resetTableState();
  }

  function toggleWeek(week: Slice) {
    const saved = findPeriod(week);
    if (!saved) return;
    setSelectedWeeks((current) => current.some((item) => item.id === week.id)
      ? current.filter((item) => item.id !== week.id)
      : [...current, { ...week, periodId: saved.id }]);
    resetTableState();
  }

  function selectTab(next: ResultTab) {
    setTab(next);
    setPage(1);
    resetTableState();
  }

  function openFilter(key: string, event: React.MouseEvent<HTMLButtonElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const left = Math.min(Math.max(8, window.innerWidth - 292), Math.max(8, rect.left - 220));
    const top = Math.max(8, Math.min(window.innerHeight - 460, rect.bottom + 6));
    setFilterRect(new DOMRect(left, top, rect.width, rect.height));
    const saved = filters[key];
    setFilterDraft(saved && isTextFilter(saved)
      ? { contains: saved.contains ?? "", selected: saved.selected ?? [], min: "", max: "" }
      : { contains: "", selected: [], min: saved?.min === undefined ? "" : String(saved.min), max: saved?.max === undefined ? "" : String(saved.max) });
    setFilterSearch("");
    setFilterError("");
    setFilterKey(key);
  }

  function applyFilter() {
    if (!filterKey) return;
    const isText = isTextColumn(filterKey);
    if (isText) {
      const contains = filterDraft.contains.trim();
      const selected = filterDraft.selected;
      setFilters((current) => {
        const next = { ...current };
        if (contains || selected.length) next[filterKey] = { contains, selected };
        else delete next[filterKey];
        return next;
      });
    } else {
      const parseBound = (raw: string) => raw.trim() === "" ? undefined : Number(raw.trim().replace(",", "."));
      const min = parseBound(filterDraft.min);
      const max = parseBound(filterDraft.max);
      if ((min !== undefined && !Number.isFinite(min)) || (max !== undefined && !Number.isFinite(max))) {
        setFilterError("Введите корректные числа.");
        return;
      }
      if (isPercent(filterKey) && ((min !== undefined && (min < 0 || min > 100)) || (max !== undefined && (max < 0 || max > 100)))) {
        setFilterError("Процент должен быть от 0 до 100.");
        return;
      }
      if (min !== undefined && max !== undefined && min > max) {
        setFilterError("Минимум не может быть больше максимума.");
        return;
      }
      setFilters((current) => {
        const next = { ...current };
        if (min !== undefined || max !== undefined) next[filterKey] = { ...(min === undefined ? {} : { min }), ...(max === undefined ? {} : { max }) };
        else delete next[filterKey];
        return next;
      });
    }
    setPage(1);
    setFilterKey(null);
  }

  function setSortFor(key: string) {
    setSort((current) => current?.key === key
      ? { key, direction: current.direction === "desc" ? "asc" : "desc" }
      : { key, direction: "desc" });
    setPage(1);
  }

  const reportRangeLabel = `${formatDate(result?.period_start ?? "")} — ${formatDate(result?.period_end ?? "")}`;
  const headingRangeLabel = overall ? `${formatDate(overall.period_start)} — ${formatDate(overall.period_end)}` : `Диапазон отчёта · ${reportRangeLabel}`;

  return (
    <section className="analytics-result" aria-label="Интерактивная аналитика">
      <header className="resultHero">
        <div>
          <h1>{groupName || "Аналитика"}</h1>
          <p>{headingRangeLabel} · аналитика №{exportId}</p>
        </div>
        <div className="resultHeroActions">
          <button className="resultButton resultButton--primary" type="button" onClick={onDownload} disabled={!canDownload} title={canDownload ? "Скачать сохранённый Excel-отчёт" : "Excel-файл недоступен; сохранённые показатели можно просмотреть здесь."}>
            <svg aria-hidden="true" viewBox="0 0 16 16" className="resultDownloadIcon"><path d="M8 2.5v7M5 7l3 3 3-3M3 12.5v1h10v-1" /></svg> {multiWeek ? "Excel всего отчёта" : "Скачать Excel"}
          </button>
          {canStartNew && <button className="resultButton" type="button" onClick={onNewAnalysis}>Новая аналитика</button>}
          <button className="resultButton" type="button" onClick={onBack}>К истории</button>
        </div>
      </header>

      {resultLoading ? <div className="resultState" role="status">Загружаю сохранённый результат…</div> : resultError ? (
        <div className="resultState resultState--error" role="alert">
          <strong>Не удалось открыть аналитику.</strong><span>{resultError}</span>
          <div className="resultStateActions"><button className="resultButton" type="button" onClick={() => setRefresh((value) => value + 1)}>Повторить</button><button className="resultButton" type="button" onClick={onBack}>К истории</button></div>
        </div>
      ) : result ? (
        <>
          <section className="resultPeriodPanel" aria-label="Периоды отчёта">
            <div className="resultControlRow">
              <span className="resultControlLabel">Период</span>
              <div className="resultSegments" role="group" aria-label="Выберите месяц">
                <button className={`resultSegment ${!multiWeek && (isGeneral || monthId === "all") ? "active" : ""} ${overall ? "" : "isUnavailable"}`} type="button" disabled={isGeneral} onClick={() => selectMonth(null)}>Весь период{!overall && <small>нет среза</small>}</button>
                <button className={`resultSegment ${multiWeek ? "active" : ""}`} type="button" aria-pressed={multiWeek} onClick={() => {
                  setMultiWeek(!multiWeek);
                  setSelectedWeeks([]);
                  if (monthId === "all" && months[0]) setMonthId(months[0].id);
                  resetTableState();
                }}>Несколько недель</button>
                {months.map((month) => {
                  const hasMonth = Boolean(findPeriod(month));
                  return <button
                    className={`resultSegment ${!isGeneral && monthId === month.id ? "active" : ""} ${hasMonth ? "" : "isUnavailable"}`}
                    type="button"
                    disabled={isGeneral}
                    title={hasMonth ? `${month.label}: ${formatDate(month.start)} — ${formatDate(month.end)}` : "Месячный срез не сохранён; проверьте недели этого месяца."}
                    onClick={() => selectMonth(month)}
                    key={month.id}
                  >{month.short}{!hasMonth && <small>нет среза</small>}</button>;
                })}
              </div>
              <span className="resultPeriodNote">{multiWeek ? selectionLabel : isGeneral ? overall ? `Общий срез: ${formatDate(overall.period_start)} — ${formatDate(overall.period_end)}` : `Общий срез не сохранён · первый срез: ${firstSavedPeriodLabel}` : `Сейчас: ${activePeriodLabel}`}</span>
            </div>
            {selectedMonth && !isGeneral && <div className="resultControlRow resultControlRow--secondary">
              <span className="resultControlLabel">Срез</span>
              <div className="resultSegments" role="group" aria-label="Выберите неделю">
                {!multiWeek && <button className={`resultSegment ${sliceId === "all" ? "active" : ""} ${findPeriod(selectedMonth) ? "" : "isUnavailable"}`} type="button" onClick={() => { setSliceId("all"); setPage(1); resetTableState(); }}>
                  Весь месяц{!findPeriod(selectedMonth) && <small>нет среза</small>}
                </button>}
                {weeks.map((week) => {
                  const hasWeek = Boolean(findPeriod(week));
                  return <button
                    className={`resultSegment ${(multiWeek ? selectedWeeks.some((item) => item.id === week.id) : sliceId === week.id) ? "active" : ""} ${hasWeek ? "" : "isUnavailable"}`}
                    type="button"
                    aria-pressed={multiWeek ? selectedWeeks.some((item) => item.id === week.id) : undefined}
                    disabled={multiWeek && !hasWeek}
                    title={hasWeek ? week.label : "Точный срез этой недели в отчёте не сохранён."}
                    onClick={() => { if (multiWeek) toggleWeek(week); else { setSliceId(week.id); setPage(1); resetTableState(); } }}
                    key={week.id}
                  >{week.short}{!hasWeek && <small>нет среза</small>}</button>;
                })}
              </div>
            </div>}
          </section>

          {multiWeek && <div className="resultInfo" aria-live="polite">
            <span>{selectionLoading ? "Рассчитываю общий итог выбранных недель…" : "Выбор сохраняется при переходе между месяцами. Пересекающиеся даты учитываются один раз."}</span>
            {selectedWeeks.length > 0 && <button className="resultButton" type="button" onClick={() => { setSelectedWeeks([]); resetTableState(); }}>Снять выбор недель</button>}
          </div>}
          {multiWeek && selectionError && <div className="resultInlineError" role="alert">{selectionError}<button type="button" onClick={() => setRefresh((value) => value + 1)}>Повторить</button></div>}
          <section className="resultKpis" aria-label="Ключевые показатели">
            {kpis.map((item) => {
              const value = metrics?.[item.key];
              const hint = item.hintKey ? metrics?.[item.hintKey] : undefined;
              return <article className={`resultKpi ${item.color}`} key={item.key}>
                <span>{item.label}</span>
                <strong>{value === undefined ? "—" : formatValue(value, item.key)}</strong>
                <small>{item.hintKey ? hint === undefined ? "Показатель не сохранён" : formatValue(hint, item.hintKey) : item.hint}</small>
              </article>;
            })}
          </section>

          <nav className="resultTabs" role="tablist" aria-label="Разделы аналитики">
            {TAB_LABELS.map((item, index) => <span className={index === 4 ? "resultTabBreak" : "resultTabItem"} key={item.id}>
              {index === 4 && <span className="resultTabDivider" aria-hidden="true" />}
              <button type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => selectTab(item.id)}>{item.label}</button>
            </span>)}
          </nav>

          {tab === "dynamics" ? (
            <section className="resultWorkspace">
              <div className="resultWorkspaceHead">
                <div><h2>Динамика</h2><p>{multiWeek ? "Выбранные недельные срезы" : selectedMonth ? `${selectedMonth.label}: сохранённые недельные срезы` : "Сохранённые месячные срезы всего диапазона"}</p></div>
                <div className="resultViewToggle" role="group" aria-label="Набор показателей">
                  <button type="button" className={view === "main" ? "active" : ""} onClick={() => { setView("main"); setPage(1); }}>Основные показатели</button>
                  <button type="button" className={view === "all" ? "active" : ""} onClick={() => { setView("all"); setPage(1); }}>Все показатели</button>
                </div>
              </div>
              <div className="resultInfo"><span>Каждая строка использует точный сохранённый период. Пересекающиеся срезы не суммируются.</span></div>
              <div className="resultToolbar">
                <label className="resultSearch"><span className="visuallyHidden">Поиск по динамике</span><input value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="Поиск по динамике..." /></label>
                <button className="resultButton" type="button" onClick={() => { setSort(null); setPage(1); }}>Сбросить сортировку</button>
                <button className="resultButton" type="button" onClick={resetFiltersAndQuery}>Сбросить фильтры</button>
              </div>
              <div className="resultChips" aria-live="polite">
                {query && <span className="resultChip">Поиск: {query}<button type="button" aria-label="Убрать общий поиск" onClick={() => { setQuery(""); setPage(1); }}>×</button></span>}
                {Object.entries(filters).map(([key, filter]) => <span className="resultChip" key={key} title={key}>{compactLabel(key)}: {filterDescription(key, filter)}<button type="button" aria-label={`Убрать фильтр ${key}`} onClick={() => { setFilters((current) => { const next = { ...current }; delete next[key]; return next; }); setPage(1); }}>×</button></span>)}
                {!query && Object.keys(filters).length === 0 && <span className="resultFilterEmpty">Фильтры не заданы</span>}
              </div>
              <ResultTable
                columns={dynamicsColumns}
                rows={visibleRows}
                stickyCount={1}
                sort={sort}
                filters={filters}
                page={page}
                pageCount={pageCount}
                total={localTotal}
                loading={false}
                emptyMessage={dynamicsRows.length ? "Показатели для выбранного периода не сохранены." : "Периодные срезы не сохранены."}
                onSort={setSortFor}
                onFilter={openFilter}
                onPage={setPage}
              />
              {filterKey && <FilterPopover key={filterKey} column={filterKey} isText={isTextColumn(filterKey)} rect={filterRect} values={availableValues(filterKey)} draft={filterDraft} search={filterSearch} error={filterError} onDraft={setFilterDraft} onSearch={setFilterSearch} onApply={applyFilter} onClear={() => { setFilters((current) => { const next = { ...current }; delete next[filterKey]; return next; }); setFilterKey(null); setPage(1); }} onClose={() => setFilterKey(null)} />}
            </section>
          ) : (
            <section className="resultWorkspace">
              <div className="resultWorkspaceHead">
                <div><h2>{({ total: "Итог", domains: "По доменам", sources: "По источникам", channels: "По каналам", statuses: "Статусы", data: "Данные" } as Record<ResultTab, string>)[tab]}</h2><p>{activePeriodLabel}</p></div>
                {!dataTable && <div className="resultViewToggle" role="group" aria-label="Набор показателей">
                  <button type="button" className={view === "main" ? "active" : ""} onClick={() => { setView("main"); setPage(1); }}>Основные показатели</button>
                  <button type="button" className={view === "all" ? "active" : ""} onClick={() => { setView("all"); setPage(1); }}>Все показатели</button>
                </div>}
              </div>
              {isGeneral && <div className="resultInfo"><span><strong>{overall ? "Общий срез." : "Общий срез не сохранён."}</strong> Здесь месяц и неделя не применяются; строки листа остаются как в сохранённом файле.</span><span>{overall ? activePeriodLabel.replace("Общий срез · ", "") : `Первый сохранённый срез: ${firstSavedPeriodLabel}`}</span></div>}
              {!multiWeek && !isGeneral && !activePeriod && <div className="resultInfo resultInfo--missing"><span>Этот срез не сохранён в отчёте. Показатели и строки недоступны.</span></div>}
              <div className="resultToolbar">
                <label className="resultSearch"><span className="visuallyHidden">Поиск по таблице</span><input value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="Поиск по таблице..." /></label>
                <button className="resultButton" type="button" onClick={() => { setSort(null); setPage(1); }}>Сбросить сортировку</button>
                <button className="resultButton" type="button" onClick={resetFiltersAndQuery}>Сбросить фильтры</button>
                <div className="resultLegend"><span>Подсветка:</span><i className="good" /><span>хорошо</span><i className="medium" /><span>средне</span><i className="bad" /><span>плохо</span></div>
              </div>
              <div className="resultChips" aria-live="polite">
                {query && <span className="resultChip">Поиск: {query}<button type="button" aria-label="Убрать общий поиск" onClick={() => { setQuery(""); setPage(1); }}>×</button></span>}
                {Object.entries(filters).map(([key, filter]) => <span className="resultChip" key={key} title={key}>{compactLabel(key)}: {filterDescription(key, filter)}<button type="button" aria-label={`Убрать фильтр ${key}`} onClick={() => { setFilters((current) => { const next = { ...current }; delete next[key]; return next; }); setPage(1); }}>×</button></span>)}
                {!query && Object.keys(filters).length === 0 && <span className="resultFilterEmpty">Фильтры не заданы</span>}
              </div>
              {tableError && <div className="resultInlineError" role="alert">{tableError}<button type="button" onClick={() => setRefresh((value) => value + 1)}>Повторить</button></div>}
              {dataTable && tableResult && !tableResult.available ? <div className="resultState resultState--compact">Строки этого листа не сохранены в доступной части отчёта. Итоговые показатели и сохранённые разрезы остаются доступны.</div> : (
                <ResultTable
                  columns={columns}
                  rows={visibleRows}
                  stickyCount={stickyCount}
                  sort={sort}
                  filters={filters}
                  page={page}
                  pageCount={pageCount}
                  total={dataTable ? tableResult?.total ?? 0 : localTotal}
                  loading={multiWeek ? selectionLoading || (dataTable && selectionIds.length > 0 && tableLoading) : dataTable && (!tableResult || tableLoading)}
                  emptyMessage={multiWeek && !selectedWeeks.length ? "Отметьте недели для общего итога." : dataTable ? "Строки по заданным условиям не найдены." : activePeriod ? "В сохранённом срезе нет строк." : "Срез не сохранён."}
                  onSort={setSortFor}
                  onFilter={openFilter}
                  onPage={setPage}
                />
              )}
              {filterKey && <FilterPopover key={filterKey} column={filterKey} isText={isTextColumn(filterKey)} rect={filterRect} values={availableValues(filterKey)} draft={filterDraft} search={filterSearch} error={filterError} onDraft={setFilterDraft} onSearch={setFilterSearch} onApply={applyFilter} onClear={() => { setFilters((current) => { const next = { ...current }; delete next[filterKey]; return next; }); setFilterKey(null); setPage(1); }} onClose={() => setFilterKey(null)} />}
            </section>
          )}
        </>
      ) : null}
    </section>
  );
}

function ResultTable({
  columns,
  rows,
  stickyCount,
  sort,
  filters,
  page,
  pageCount,
  total,
  loading,
  emptyMessage,
  onSort,
  onFilter,
  onPage,
}: {
  columns: string[];
  rows: AnalyticsRow[];
  stickyCount: number;
  sort: SortState;
  filters: Record<string, SavedResultFilter>;
  page: number;
  pageCount: number;
  total: number;
  loading: boolean;
  emptyMessage: string;
  onSort: (key: string) => void;
  onFilter: (key: string, event: MouseEvent<HTMLButtonElement>) => void;
  onPage: (page: number) => void;
}) {
  const widths = useRef<Record<string, number>>({});
  const resize = useRef<{ key: string; startX: number; startWidth: number } | null>(null);
  const [widthVersion, setWidthVersion] = useState(0);
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const updateWidth = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", updateWidth);
    return () => window.removeEventListener("resize", updateWidth);
  }, []);
  const mobile = viewportWidth <= 620;
  const getWidth = (key: string) => widths.current[key] ?? (key === "Полный источник" || key === "Домен" ? mobile ? 130 : 240 : key === "Период" ? 160 : ["Канал", "Группа статуса"].includes(key) ? mobile ? 64 : 76 : isPercent(key) ? 82 : typeof rows[0]?.[key] === "string" ? 180 : 84);
  const groups = columns.reduce<{ label: string; span: number; start: number }[]>((items, key, index) => {
    const label = groupFor(key);
    const previous = items.at(-1);
    if (previous?.label === label) previous.span += 1;
    else items.push({ label, span: 1, start: index });
    return items;
  }, []);
  const stickyLeft = (index: number) => columns.slice(0, index).reduce((sum, key) => sum + getWidth(key), 0);
  return <>
    <div className="resultTableShell">
      <table className="resultTable">
        <colgroup>{columns.map((key) => <col key={key} style={{ width: getWidth(key) }} />)}</colgroup>
        <thead>
          <tr className="resultGroupRow">
            {groups.map((group) => <th key={`${group.label}-${group.start}`} colSpan={group.span} className="resultGroup" data-group={group.label} style={group.start < stickyCount ? { left: 0, zIndex: 5 } : undefined}>{group.label}</th>)}
          </tr>
          <tr className="resultHeaderRow">
            {columns.map((key, index) => <th key={key} className={index < stickyCount ? `resultSticky resultSticky--${index}` : ""} style={index < stickyCount ? { left: stickyLeft(index), zIndex: 6 } : undefined}>
              <div className="resultHeaderCell">
                <button className="resultSortButton" type="button" title={`Сортировать: ${key}`} onClick={() => onSort(key)}>
                  <span title={key}>{compactLabel(key)}</span>{sort?.key === key && <svg aria-label={sort.direction === "desc" ? "По убыванию" : "По возрастанию"} viewBox="0 0 12 12" className={`resultSortMark ${sort.direction}`}><path d="M6 1.5v8M2.8 6.5 6 9.7l3.2-3.2" /></svg>}
                </button>
                <button className={`resultFilterButton ${filters[key] ? "active" : ""}`} type="button" aria-label={`Фильтр: ${key}`} title={`Фильтр: ${key}`} onClick={(event) => onFilter(key, event)}><MetricIcon /></button>
                <span className="resultResizeHandle" role="separator" aria-orientation="vertical" aria-label={`Изменить ширину: ${key}`} onPointerDown={(event) => {
                  event.preventDefault();
                  event.currentTarget.setPointerCapture(event.pointerId);
                  resize.current = { key, startX: event.clientX, startWidth: getWidth(key) };
                }} onPointerMove={(event) => {
                  if (!resize.current || resize.current.key !== key) return;
                  widths.current[key] = Math.max(58, Math.min(460, resize.current.startWidth + event.clientX - resize.current.startX));
                  setWidthVersion((value) => value + 1);
                }} onPointerUp={() => { resize.current = null; }} tabIndex={0} onKeyDown={(event) => {
                  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                  event.preventDefault();
                  widths.current[key] = Math.max(58, Math.min(460, getWidth(key) + (event.key === "ArrowRight" ? 12 : -12)));
                  setWidthVersion((value) => value + 1);
                }} />
              </div>
            </th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => <tr key={`${String(row[columns[0]] ?? "row")}-${rowIndex}`}>
            {columns.map((key, index) => {
              const unavailable = Boolean(row._unavailable);
              const fill = (row._fills as Record<string, string> | undefined)?.[key];
              const value = row[key];
              return <td key={`${key}-${index}`} className={`${index < stickyCount ? `resultSticky resultSticky--${index}` : ""} ${typeof value === "string" ? "resultTextCell" : "resultNumericCell"}`} style={{ ...(index < stickyCount ? { left: stickyLeft(index), zIndex: 2 } : {}), ...fillColors(fill) }} title={unavailable ? "Срез не сохранён" : value === undefined ? "" : String(value)}>
                {unavailable && index === 0 ? <span className="resultMissingCell">{String(value ?? "Срез не сохранён")}</span> : unavailable ? "—" : formatValue(value, key)}
              </td>;
            })}
          </tr>)}
          {!loading && rows.length === 0 && <tr><td className="resultEmptyCell" colSpan={Math.max(columns.length, 1)}>{emptyMessage}</td></tr>}
          {loading && rows.length === 0 && <tr><td className="resultEmptyCell" colSpan={Math.max(columns.length, 1)}>Загружаю строки…</td></tr>}
        </tbody>
      </table>
      {columns.length === 0 && !loading && <div className="resultEmptyCell">Показатели не сохранены для этого отчёта.</div>}
      <span className="visuallyHidden" aria-live="polite">{widthVersion > 0 ? "Ширина колонки изменена" : ""}</span>
    </div>
    <div className="resultTableFooter">
      <span>{loading ? "Обновляю таблицу…" : total ? `Показано ${(page - 1) * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE, total)} из ${total} строк` : "Показано 0 строк"}</span>
      <div className="resultPager">
        <button type="button" aria-label="Предыдущая страница" disabled={page <= 1 || loading} onClick={() => onPage(Math.max(1, page - 1))}>Назад</button>
        <span>{page} / {pageCount}</span>
        <button type="button" aria-label="Следующая страница" disabled={page >= pageCount || loading} onClick={() => onPage(Math.min(pageCount, page + 1))}>Дальше</button>
      </div>
    </div>
  </>;
}

function FilterPopover({
  column,
  isText,
  rect,
  values,
  draft,
  search,
  error,
  onDraft,
  onSearch,
  onApply,
  onClear,
  onClose,
}: {
  column: string;
  isText: boolean;
  rect: DOMRect | null;
  values: string[];
  draft: FilterDraft;
  search: string;
  error: string;
  onDraft: (draft: FilterDraft) => void;
  onSearch: (value: string) => void;
  onApply: () => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const lowerSearch = search.toLocaleLowerCase("ru-RU");
  const filteredValues = values.filter((value) => value.toLocaleLowerCase("ru-RU").includes(lowerSearch));
  const shownValues = filteredValues.slice(0, 100);
  const selected = new Set(draft.selected);

  return <>
    <button className="resultPopoverScrim" type="button" aria-label="Закрыть фильтр" onClick={onClose} />
    <section className="resultFilterPopover" role="dialog" aria-label={`Фильтр: ${column}`} style={rect ? { top: rect.top, left: rect.left } : undefined}>
      <div className="resultPopoverTitle"><strong title={column}>{column}</strong><button type="button" aria-label="Закрыть" onClick={onClose}>×</button></div>
      {isText ? <>
        <label className="visuallyHidden" htmlFor="result-filter-contains">Содержит текст</label>
        <input id="result-filter-contains" className="resultPopoverInput" value={draft.contains} onChange={(event) => onDraft({ ...draft, contains: event.target.value })} placeholder="Содержит текст..." autoFocus />
        <label className="visuallyHidden" htmlFor="result-filter-value-search">Найти значение</label>
        <input id="result-filter-value-search" className="resultPopoverInput" value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Найти значение..." />
        <div className="resultValueList">
          {shownValues.map((value) => <label className="resultValueOption" key={value} title={value || "(пусто)"}>
            <input type="checkbox" checked={selected.has(value)} onChange={() => onDraft({ ...draft, selected: selected.has(value) ? draft.selected.filter((item) => item !== value) : [...draft.selected, value] })} />
            <span>{value || "(пусто)"}</span>
          </label>)}
          {filteredValues.length === 0 && <span className="resultValueEmpty">Значения не найдены</span>}
        </div>
        {filteredValues.length > shownValues.length && <small className="resultPopoverHint">Показаны первые {shownValues.length} из {filteredValues.length}. Уточните поиск.</small>}
      </> : <>
        <div className="resultRangeGrid">
          <label>От<input className="resultPopoverInput" inputMode="decimal" value={draft.min} onChange={(event) => onDraft({ ...draft, min: event.target.value })} /></label>
          <label>До<input className="resultPopoverInput" inputMode="decimal" value={draft.max} onChange={(event) => onDraft({ ...draft, max: event.target.value })} /></label>
        </div>
        <small className="resultPopoverHint">{isPercent(column) ? "Введите проценты от 0 до 100." : "Введите числовой диапазон."}</small>
      </>}
      {error && <span className="resultFilterError" role="alert">{error}</span>}
      <div className="resultPopoverActions"><button type="button" onClick={onClear}>Сбросить</button><button className="resultButton--primary" type="button" onClick={onApply}>Применить</button></div>
    </section>
  </>;
}
