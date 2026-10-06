import { useEffect, useId, useMemo, useState, type DragEvent } from "react";
import type {
  AnalyzeSetup,
  ExportRecord,
  FileInspect,
  Mapping,
  OperationStage,
  SheetPreview,
  StatusRulesData,
  Step,
  WorkbookPreview
} from "./types";

type MappingFile = FileInspect | AnalyzeSetup;

const MATCHED_SHEET_NAME = "Сопоставленные";

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

function activeSheet(file: MappingFile | WorkbookPreview | null, sheetName: string): SheetPreview | null {
  if (!file) return null;
  return file.sheets.find((sheet) => sheet.name === sheetName) ?? file.sheets[0] ?? null;
}

function sheetColumns(file: MappingFile | null, mapping: Mapping): string[] {
  if (!file) return [];
  return file.sheets.find((sheet) => sheet.name === mapping.sheet_name)?.columns ?? [];
}

function sheetRowsLabel(sheet: SheetPreview): string {
  const shown = Math.min(sheet.rows.length, 5);
  return shown ? `${shown} строк в предпросмотре` : "нет строк в предпросмотре";
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.replace(",", "."));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4);
  return String(value);
}

function valueFromCheckSheet(workbook: WorkbookPreview, key: string): string {
  const sheet = workbook.sheets.find((item) => item.name === "Проверка");
  const row = sheet?.rows.find((item) => item["Показатель"] === key);
  return displayValue(row?.["Значение"]);
}

function firstRow(workbook: WorkbookPreview, sheetName: string): Record<string, unknown> {
  return workbook.sheets.find((item) => item.name === sheetName)?.rows[0] ?? {};
}

function fileLabel(file: File | null): string {
  if (!file) return "Файл не выбран";
  const sizeMb = file.size / 1024 / 1024;
  return `${file.name} · ${sizeMb >= 1 ? sizeMb.toFixed(1) : "<1"} МБ`;
}

function validExcelFile(file: File): boolean {
  return file.name.toLowerCase().endsWith(".xlsx");
}

export function FileDropZone({
  label,
  file,
  spreadsheetUrl = "",
  disabled = false,
  onChange,
  onSpreadsheetUrlChange
}: {
  label: string;
  file: File | null;
  spreadsheetUrl?: string;
  disabled?: boolean;
  onChange: (file: File | null) => void;
  onSpreadsheetUrlChange?: (url: string) => void;
}) {
  const inputId = useId();
  const urlId = useId();
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");

  function selectFile(nextFile: File | null) {
    if (nextFile && !validExcelFile(nextFile)) {
      setError("Нужен файл .xlsx");
      return;
    }
    setError("");
    onChange(nextFile);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    selectFile(event.dataTransfer.files?.[0] ?? null);
  }

  return (
    <div className="filePicker">
      <span>{label}</span>
      <label
        className={`dropZone ${dragging ? "dragging" : ""} ${file ? "hasFile" : ""}`}
        htmlFor={inputId}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        <input
          id={inputId}
          type="file"
          accept=".xlsx"
          disabled={disabled}
          onChange={(event) => {
            selectFile(event.target.files?.[0] ?? null);
            event.target.value = "";
          }}
        />
        <span className="fileBadge">XLSX</span>
        <strong>{file ? file.name : "Выберите или перетащите файл"}</strong>
        <small>{fileLabel(file)}</small>
      </label>
      {file && <button className="ghostButton fileClear" type="button" disabled={disabled} onClick={() => selectFile(null)}>Убрать выбранный файл</button>}
      {error && <small className="fieldError">{error}</small>}
      {onSpreadsheetUrlChange && (
        <label className="field" htmlFor={urlId}>
          <span>Или ссылка на Google-таблицу</span>
          <input
            id={urlId}
            type="url"
            value={spreadsheetUrl}
            placeholder="https://docs.google.com/spreadsheets/d/..."
            disabled={disabled}
            onChange={(event) => onSpreadsheetUrlChange(event.target.value)}
          />
        </label>
      )}
    </div>
  );
}

export function ProcessProgress({
  active,
  stage,
  label,
  processedRows = 0,
  totalRows = 0,
  queued = false
}: {
  active: boolean;
  stage: OperationStage | null;
  label: string;
  processedRows?: number;
  totalRows?: number;
  queued?: boolean;
}) {
  if (!active || !stage) return null;
  const stages: { id: OperationStage; title: string }[] = [
    { id: "upload", title: "Загрузка" },
    { id: "read", title: "Чтение" },
    { id: "match", title: "Сопоставление" },
    { id: "prepare", title: "Подготовка аналитики" },
    { id: "done", title: "Готово" }
  ];
  const current = Math.max(0, stages.findIndex((item) => item.id === stage));
  const remaining = Math.max(0, stages.length - current - 1);
  const actualProgress = totalRows > 0 ? Math.min(100, (processedRows / totalRows) * 100) : null;

  return (
    <section className="processPanel" aria-live="polite">
      <div className="processHeader">
        <div>
          <h2>{label || stages[current].title}</h2>
          <p>
            {queued
              ? "Ожидает выполнения в очереди"
              : actualProgress !== null
                ? `${processedRows} из ${totalRows} строк`
                : remaining
                  ? `Осталось этапов: ${remaining}`
                  : "Завершаю обработку"}
          </p>
        </div>
        <div className="spinner" aria-hidden="true" />
      </div>
      <div className="processTrack" aria-hidden="true">
        <span style={{ width: `${actualProgress ?? ((current + 1) / stages.length) * 100}%` }} />
      </div>
      <div className="processSteps">
        {stages.map((item, index) => (
          <div className={`processStep ${index < current ? "done" : ""} ${index === current ? "active" : ""}`} key={item.id}>
            <span>{index + 1}</span>
            <strong>{item.title}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

export function SelectField({
  label,
  value,
  columns,
  required = false,
  note,
  onChange
}: {
  label: string;
  value?: string | null;
  columns: string[];
  required?: boolean;
  note?: string;
  onChange: (value: string | null) => void;
}) {
  return (
    <label className={`field mappingField ${required && !value ? "mappingWarning" : ""}`}>
      <span>
        {label}
        {required ? " *" : ""}
      </span>
      <select value={value ?? ""} onChange={(event) => onChange(event.target.value || null)}>
        <option value="">Не выбрано</option>
        {columns.map((column) => (
          <option value={column} key={column}>
            {column}
          </option>
        ))}
      </select>
      <small className={note?.startsWith("Проверьте") || note?.startsWith("Обязательное") ? "fieldError mappingFeedback" : "mappingNote mappingFeedback"} title={note}>{note || "\u00a0"}</small>
    </label>
  );
}

export function Stepper({
  step,
  historyOpen,
  disabled,
  canVisit,
  onStepChange,
  onHistoryChange
}: {
  step: Step;
  historyOpen: boolean;
  disabled: boolean;
  canVisit: Record<Step, boolean>;
  onStepChange: (step: Step) => void;
  onHistoryChange: (open: boolean) => void;
}) {
  const steps: { id: Step; title: string }[] = [
    { id: "upload", title: "Файлы" },
    { id: "mapping", title: "Колонки" },
    { id: "analyze", title: "Аналитика" },
    { id: "done", title: "Результат" }
  ];
  const current = steps.findIndex((item) => item.id === step);

  return (
    <nav className="stepper" aria-label="Шаги обработки">
      {steps.map((item, index) => (
        <button
          type="button"
          className={`stepItem ${!historyOpen && index === current ? "active" : ""} ${index < current ? "done" : ""}`}
          aria-current={!historyOpen && index === current ? "step" : undefined}
          disabled={disabled || !canVisit[item.id]}
          onClick={() => onStepChange(item.id)}
          key={item.id}
        >
          <span>{index + 1}</span>
          <strong>{item.title}</strong>
        </button>
      ))}
      <button type="button" className={`stepItem ${historyOpen ? "active" : ""}`} aria-current={historyOpen ? "page" : undefined} disabled={disabled} onClick={() => onHistoryChange(true)}>
        <strong>История</strong>
      </button>
    </nav>
  );
}

export function SheetTabs({
  sheets,
  value,
  onChange
}: {
  sheets: SheetPreview[];
  value: string;
  onChange: (sheetName: string) => void;
}) {
  if (sheets.length <= 1) return null;
  return (
    <div className="sheetTabs" role="tablist">
      {sheets.map((sheet) => (
        <button
          className={sheet.name === value ? "active" : ""}
          type="button"
          role="tab"
          aria-selected={sheet.name === value}
          onClick={() => onChange(sheet.name)}
          key={sheet.name}
        >
          {sheet.name}
        </button>
      ))}
    </div>
  );
}

export function PreviewTable({ sheet }: { sheet: SheetPreview }) {
  const [query, setQuery] = useState("");
  const columns = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const selected = normalized ? sheet.columns.filter((column) => column.toLowerCase().includes(normalized)) : sheet.columns;
    return selected.slice(0, 14);
  }, [query, sheet.columns]);

  return (
    <div className="preview">
      <div className="previewHeader">
        <div>
          <div className="previewTitle">{sheet.name}</div>
          <p>{sheetRowsLabel(sheet)}</p>
        </div>
        <input
          className="columnSearch"
          value={query}
          placeholder="Поиск колонки"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="tableWrap">
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sheet.rows.slice(0, 5).map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column} title={String(row[column] ?? "")}>{String(row[column] ?? "")}</td>
                ))}
              </tr>
            ))}
            {columns.length === 0 && (
              <tr>
                <td>Колонки не найдены</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function MappingPanel({
  title,
  file,
  mapping,
  role,
  onChange
}: {
  title: string;
  file: MappingFile;
  mapping: Mapping;
  role: "lk" | "client" | "analyze";
  onChange: (mapping: Mapping) => void;
}) {
  const lockAnalyzeSheet = role === "analyze" && file.sheets.some((sheet) => sheet.name === MATCHED_SHEET_NAME);
  const sheets = lockAnalyzeSheet
    ? file.sheets.filter((sheet) => sheet.name === MATCHED_SHEET_NAME)
    : file.sheets;
  const effectiveMapping = lockAnalyzeSheet ? { ...mapping, sheet_name: MATCHED_SHEET_NAME } : mapping;
  const columns = sheetColumns(file, effectiveMapping);
  const active = activeSheet(file, effectiveMapping.sheet_name);
  const detected = "detected" in file ? file.detected : file.mapping;

  function patch(update: Partial<Mapping>) {
    onChange({ ...effectiveMapping, ...update });
  }

  function field(label: string, key: keyof Mapping, required = false) {
    const value = mapping[key];
    const sample = active?.rows.map((row) => displayValue(row[String(value)])).find((item) => item.trim());
    const note = !value
      ? required ? "Обязательное поле не выбрано" : undefined
      : !columns.includes(value) ? "Проверьте: колонки нет на выбранном листе"
      : !sample ? "Проверьте: в предпросмотре нет значений"
      : detected[key] === value ? "Определено автоматически" : "Выбрано вручную";
    return <SelectField key={key} label={label} value={value} columns={columns} required={required} note={note} onChange={(next) => patch({ [key]: next })} />;
  }

  const requiredFields: Array<[string, keyof Mapping]> = role === "lk"
    ? [["LKID", "lkid_column"], ["Полный источник", "source_column"]]
    : role === "client" ? [["Статус", "status_column"]] : [["Статус", "status_column"], ["Дата", "date_column"]];
  const matchingFields: Array<[string, keyof Mapping]> = role === "lk"
    ? [["Телефон", "phone_column"]]
    : role === "client" ? [["LKID", "lkid_column"], ["Источник", "source_column"], ["Телефон", "phone_column"]] : [];
  const optionalFields: Array<[string, keyof Mapping]> = role === "lk"
    ? [["Дата", "date_column"]]
    : role === "client"
      ? [["Дата", "date_column"], ["Комментарий", "comment_column"]]
      : [["Полный источник", "source_column"], ["Канал", "channel_column"], ["Телефон", "phone_column"], ["Комментарий", "comment_column"]];
  const selectedOptionalCount = optionalFields.filter(([, key]) => mapping[key]).length;

  return (
    <section className="panel mappingPanel">
      <div className="panelHeader">
        <div>
          <h2>{title}</h2>
          <p title={file.filename}>{file.filename}</p>
        </div>
      </div>
      <div className="mappingGrid">
        {(
          <label className="field">
            <span>Лист</span>
            <select disabled={sheets.length <= 1} value={effectiveMapping.sheet_name} onChange={(event) => patch({ sheet_name: event.target.value })}>
              {sheets.map((sheet) => <option value={sheet.name} key={sheet.name}>{sheet.name}</option>)}
            </select>
          </label>
        )}
        <div className="mappingFields">
          {requiredFields.map(([label, key]) => field(label, key, true))}
          {matchingFields.map(([label, key]) => field(label, key))}
        </div>
        <details className="optionalMappings">
          <summary>Дополнительные колонки <span>{selectedOptionalCount} выбрано</span></summary>
          <div className="mappingFields">{optionalFields.map(([label, key]) => field(label, key))}</div>
        </details>
      </div>
      {active && <PreviewTable sheet={active} />}
    </section>
  );
}

export function WorkbookViewer({ title, workbook }: { title: string; workbook: WorkbookPreview }) {
  const [sheetName, setSheetName] = useState(workbook.sheets[0]?.name ?? "");
  const sheet = activeSheet(workbook, sheetName);

  return (
    <section className="panel">
      <div className="panelHeader compact">
        <div>
          <h2>{title}</h2>
          <p>{workbook.filename}</p>
        </div>
      </div>
      <SheetTabs sheets={workbook.sheets} value={sheet?.name ?? ""} onChange={setSheetName} />
      {sheet && <PreviewTable sheet={sheet} />}
    </section>
  );
}

export function MatchSummary({ workbook }: { workbook: WorkbookPreview }) {
  const total = valueFromCheckSheet(workbook, "Строк в ЛК");
  const matched = valueFromCheckSheet(workbook, "Сопоставлено");
  const unmatched = valueFromCheckSheet(workbook, "Не сопоставлено из ЛК");
  const matchedNumber = toNumber(matched);
  const totalNumber = toNumber(total);
  const rate = matchedNumber !== null && totalNumber ? matchedNumber / totalNumber : null;

  return (
    <div className="summaryGrid matchSummary">
      <MetricCard label="Сопоставлено" value={matched || "0"} hint={rate !== null ? formatPercent(rate) : undefined} />
      <MetricCard label="Строк в ЛК" value={total || "0"} />
      <MetricCard label="Не найдено из ЛК" value={unmatched || "0"} />
      <MetricCard label="Неоднозначные сопоставления" value={valueFromCheckSheet(workbook, "Неоднозначные сопоставления") || "0"} tooltip="Для строки ЛК найдено несколько записей клиента по использованному ключу. Выбрана последняя по дате; строка ЛК учитывается один раз. Подробности в Excel сопоставления." />
    </div>
  );
}

export function AnalyzeSummary({ workbook }: { workbook: WorkbookPreview }) {
  const row = firstRow(workbook, "Итог");
  return (
    <div className="summaryGrid analyticsSummary">
      <MetricCard label="Всего идентификаций" value={displayValue(row["Всего идентификаций"]) || "0"} />
      <MetricCard label="Качественные" value={displayValue(row["Качественные"]) || "0"} hint={formatMetric(row["Кач. %"])} />
      <MetricCard label="Недозвон" value={displayValue(row["Недозвон"]) || "0"} hint={formatMetric(row["Недозвон %"])} />
      <MetricCard label="Сигнал спроса" value={displayValue(row["Сигнал спроса"]) || "0"} hint={formatMetric(row["Сигнал спроса %"])} />
      <MetricCard label="Конкурент" value={displayValue(row["Конкурент"]) || "0"} hint={formatMetric(row["Конкурент %"])} />
    </div>
  );
}

function formatMetric(value: unknown): string | undefined {
  const numeric = toNumber(value);
  return numeric === null ? undefined : formatPercent(numeric);
}

function MetricCard({ label, value, hint, tooltip }: { label: string; value: string; hint?: string; tooltip?: string }) {
  return (
    <div className="metricCard">
      <span title={tooltip} tabIndex={tooltip ? 0 : undefined}>{label}{tooltip && " ⓘ"}</span>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </div>
  );
}

export function StatusRulesPanel({
  setup,
  statusRules,
  onChange
}: {
  setup: AnalyzeSetup;
  statusRules: Record<string, string>;
  onChange: (rules: Record<string, string>) => void;
}) {
  if (setup.unknown_statuses.length === 0) return null;
  return (
    <section className="panel">
      <div className="panelHeader compact">
        <div>
          <h2>Неизвестные статусы</h2>
          <p>Выберите группу для каждого статуса</p>
        </div>
      </div>
      <div className="rulesGrid">
        {setup.unknown_statuses.map((status) => (
          <label className="ruleRow" key={status}>
            <span>{status}</span>
            <select
              value={statusRules[status] ?? ""}
              onChange={(event) => onChange({ ...statusRules, [status]: event.target.value })}
            >
              <option value="" disabled>
                Выберите группу
              </option>
              {setup.status_groups.map((group) => (
                <option value={group} key={group}>
                  {group}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    </section>
  );
}

export function StatusRulesModal({
  open,
  setup,
  statusRules,
  loading,
  onChange,
  onCancel,
  onConfirm
}: {
  open: boolean;
  setup: AnalyzeSetup;
  statusRules: Record<string, string>;
  loading: boolean;
  onChange: (rules: Record<string, string>) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!open) return null;
  const count = setup.unknown_statuses.length;
  const missingCount = setup.unknown_statuses.filter((status) => !statusRules[status]).length;

  return (
    <div className="modalOverlay" role="dialog" aria-modal="true" aria-labelledby="status-modal-title">
      <section className="modalPanel">
        <div className="modalHeader">
          <div>
            <h2 id="status-modal-title">Проверьте статусы перед аналитикой</h2>
            <p>{count} {count === 1 ? "статус требует" : "статуса требуют"} ручного выбора группы</p>
            <p>Выбранная группа влияет на показатели отчёта и сохранится для этого набора проектов.</p>
          </div>
          <button className="ghostButton iconButton" type="button" disabled={loading} onClick={onCancel} aria-label="Закрыть">
            x
          </button>
        </div>
        <div className="rulesGrid modalRules">
          {setup.unknown_statuses.map((status) => (
            <label className="ruleRow" key={status}>
              <span>{status}<small className="statusCount">{setup.unknown_status_counts[status] ?? 0} строк в файле</small></span>
              <select
                value={statusRules[status] ?? ""}
                disabled={loading}
                onChange={(event) => onChange({ ...statusRules, [status]: event.target.value })}
              >
                <option value="" disabled>
                  Выберите группу
                </option>
                {setup.status_groups.map((group) => (
                  <option value={group} key={group}>
                    {group}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        {missingCount > 0 && (
          <p className="formHint">Осталось выбрать групп: {missingCount}</p>
        )}
        <div className="modalFooter">
          <button className="ghostButton" type="button" disabled={loading} onClick={onCancel}>
            Вернуться
          </button>
          <button type="button" disabled={loading || missingCount > 0} onClick={onConfirm}>
            Запустить аналитику
          </button>
        </div>
      </section>
    </div>
  );
}

export function StatusRulesManager({
  open,
  client,
  data,
  loading,
  error,
  onClose,
  onSave,
  onDelete,
  onCreate,
  onResolve
}: {
  open: boolean;
  client: string;
  data: StatusRulesData | null;
  loading: boolean;
  error: string;
  onClose: () => void;
  onSave: (ruleId: number, groupName: string) => Promise<void>;
  onDelete: (ruleId: number) => Promise<void>;
  onCreate: (pattern: string, groupName: string) => Promise<boolean>;
  onResolve: (pattern: string, groupName: string) => Promise<void>;
}) {
  const [search, setSearch] = useState("");
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [conflictDrafts, setConflictDrafts] = useState<Record<string, string>>({});
  const [newPattern, setNewPattern] = useState("");
  const [newGroup, setNewGroup] = useState("");
  const [deletingRule, setDeletingRule] = useState<number | null>(null);

  useEffect(() => {
    setDrafts(
      Object.fromEntries(
        (data?.project_rules ?? [])
          .filter((rule) => rule.id !== null)
          .map((rule) => [rule.id as number, rule.group_name])
      )
    );
    setConflictDrafts(Object.fromEntries((data?.conflicts ?? []).map((conflict) => [conflict.pattern, ""])));
    setNewGroup((current) => data?.status_groups.includes(current) ? current : data?.status_groups[0] ?? "");
  }, [data]);

  useEffect(() => {
    if (!open) {
      setSearch("");
      setDeletingRule(null);
      setNewPattern("");
      setConflictDrafts({});
    }
  }, [open]);

  if (!open) return null;
  const query = search.trim().toLocaleLowerCase("ru");
  const matches = (pattern: string, groups: string[]) =>
    !query ||
    pattern.toLocaleLowerCase("ru").includes(query) ||
    groups.some((group) => group.toLocaleLowerCase("ru").includes(query));
  const clientRules = (data?.project_rules ?? []).filter((rule) => matches(rule.pattern, [rule.group_name]));
  const conflicts = (data?.conflicts ?? []).filter((conflict) => matches(conflict.pattern, conflict.group_names));

  return (
    <div className="modalOverlay" role="dialog" aria-modal="true" aria-labelledby="rules-manager-title">
      <section className="modalPanel rulesManagerModal">
        <div className="modalHeader">
          <div>
            <h2 id="rules-manager-title">Соответствия статусов клиента</h2>
            <p>{client}</p>
          </div>
          <button className="ghostButton iconButton" type="button" disabled={loading} onClick={onClose} aria-label="Закрыть">
            x
          </button>
        </div>

        <p>Правила применяются ко всем группам клиента в следующих запусках. Готовые отчёты сохраняются.</p>
        {error && <div className="alert" role="alert">{error}</div>}

        <label className="field rulesSearch">
          <span>Поиск по статусу или категории</span>
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Начните вводить..." />
        </label>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (await onCreate(newPattern.trim(), newGroup)) setNewPattern("");
          }}
        >
          <div className="rulesSectionHeader">
            <div>
              <h2>Добавить правило</h2>
              <p>Новое соответствие сразу станет доступно во всех группах клиента.</p>
            </div>
          </div>
          <div className="twoColumn">
            <label className="field">
              <span>Исходный статус</span>
              <input value={newPattern} onChange={(event) => setNewPattern(event.target.value)} required />
            </label>
            <label className="field">
              <span>Категория для клиента</span>
              <select value={newGroup} onChange={(event) => setNewGroup(event.target.value)} required>
                <option value="">Выберите категорию</option>
                {(data?.status_groups ?? []).map((group) => <option value={group} key={group}>{group}</option>)}
              </select>
            </label>
          </div>
          <div className="ruleActions">
            <button type="submit" disabled={loading || !newPattern.trim() || !newGroup}>Добавить правило</button>
          </div>
        </form>

        {conflicts.length > 0 && (
          <>
            <div className="rulesSectionHeader">
              <div>
                <h2>Нужно разрешить расхождения</h2>
                <p>В группах клиента для этих статусов были сохранены разные категории.</p>
              </div>
              <strong>{conflicts.length}</strong>
            </div>
            <div className="tableWrap rulesTableWrap">
              <table className="rulesTable">
                <thead>
                  <tr><th>Исходный статус</th><th>Категории в группах</th><th>Категория клиента</th><th>Действия</th></tr>
                </thead>
                <tbody>
                  {conflicts.map((conflict) => {
                    const draft = conflictDrafts[conflict.pattern] ?? "";
                    return (
                      <tr key={conflict.pattern}>
                        <td title={conflict.pattern}>{conflict.pattern}</td>
                        <td>{conflict.group_names.join(" · ")}</td>
                        <td>
                          <select
                            aria-label={`Категория для статуса ${conflict.pattern}`}
                            value={draft}
                            disabled={loading}
                            onChange={(event) => setConflictDrafts({ ...conflictDrafts, [conflict.pattern]: event.target.value })}
                          >
                            <option value="">Выберите категорию</option>
                            {(data?.status_groups ?? []).map((group) => <option value={group} key={group}>{group}</option>)}
                          </select>
                        </td>
                        <td>
                          <button type="button" disabled={loading || !draft} onClick={() => void onResolve(conflict.pattern, draft)}>
                            Сохранить выбор
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}

        <div className="rulesSectionHeader">
          <div>
            <h2>Правила клиента</h2>
            <p>Изменения применяются ко всем группам в будущих запусках.</p>
          </div>
          <strong>{clientRules.length}</strong>
        </div>
        {clientRules.length === 0 ? (
          <div className="emptyState">
            <strong>Правила не найдены</strong>
            <span>{query ? "Измените строку поиска." : "Для этого клиента пока нет сохранённых правил."}</span>
          </div>
        ) : (
          <div className="tableWrap rulesTableWrap">
            <table className="rulesTable">
              <thead>
                <tr>
                  <th>Исходный статус</th>
                  <th>Группа</th>
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {clientRules.map((rule) => {
                  const ruleId = rule.id as number;
                  const draft = drafts[ruleId] ?? rule.group_name;
                  const changed = draft !== rule.group_name;
                  return (
                    <tr key={ruleId}>
                      <td title={rule.pattern}>{rule.pattern}</td>
                      <td>
                        <select
                          value={draft}
                          disabled={loading}
                          onChange={(event) => setDrafts({ ...drafts, [ruleId]: event.target.value })}
                        >
                          {(data?.status_groups ?? []).map((group) => (
                            <option value={group} key={group}>{group}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <div className="ruleActions">
                          <button
                            type="button"
                            disabled={loading || !changed}
                            onClick={() => onSave(ruleId, draft)}
                          >
                            Сохранить
                          </button>
                          <button
                            className="dangerButton"
                            type="button"
                            disabled={loading}
                            onClick={() => setDeletingRule(ruleId)}
                          >
                            Удалить
                          </button>
                        </div>
                        {deletingRule === ruleId && (
                          <div className="inlineConfirm">
                            <span>Удалить правило для всех групп клиента?</span>
                            <button
                              className="dangerButton"
                              type="button"
                              disabled={loading}
                              onClick={async () => {
                                await onDelete(ruleId);
                                setDeletingRule(null);
                              }}
                            >
                              Да
                            </button>
                            <button
                              className="ghostButton"
                              type="button"
                              disabled={loading}
                              onClick={() => setDeletingRule(null)}
                            >
                              Нет
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="modalFooter">
          <span />
          <button className="ghostButton" type="button" disabled={loading} onClick={onClose}>Закрыть</button>
        </div>
      </section>
    </div>
  );
}

export function ExportHistory({
  groupName,
  exports,
  loading,
  deletingExport,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
  onDownload
}: {
  groupName: string;
  exports: ExportRecord[];
  loading: boolean;
  deletingExport: number | null;
  onAskDelete: (exportId: number) => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
  onDownload: (exportId: number) => void;
}) {
  const hasGroup = groupName.trim().length > 0;

  return (
    <section className="panel historyPanel">
      <div className="panelHeader">
        <div>
          <h2>История аналитики</h2>
          <p>{hasGroup ? `Сохранённые отчёты · ${groupName}` : "Выберите группу проектов"}</p>
        </div>
      </div>
      {!hasGroup ? null : exports.length === 0 ? (
        <div className="emptyState">
          <strong>Сохранённых выгрузок пока нет.</strong>
          <span>После первой аналитики здесь появятся периоды, файлы и ключевые показатели.</span>
        </div>
      ) : (
        <div className="tableWrap historyWrap">
          <table>
            <thead>
              <tr>
                <th>№</th>
                <th>Период</th>
                <th>Дата анализа</th>
                <th>Файл</th>
                <th>Всего</th>
                <th>Недозвон</th>
                <th>Качественные</th>
                <th>Сигнал спроса</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {exports.map((item) => (
                <tr key={item.id}>
          <td>{item.export_number}</td>
                  <td>
                    {item.periods.map((period) => (
                      <div key={`${period.period_start}-${period.period_end}`}>
                        {period.period_start} - {period.period_end}
                      </div>
                    ))}
                  </td>
                  <td>{item.analysis_date}</td>
                  <td>{item.source_file_name}</td>
                  <td>{item.periods.map((period) => <div key={`${period.period_start}-${period.period_end}`}>{period.total_count}</div>)}</td>
                  <td>
                    {item.periods.map((period) => (
                      <div key={`${period.period_start}-${period.period_end}`}>
                        {period.missed_count} / {formatPercent(period.missed_rate)}
                      </div>
                    ))}
                  </td>
                  <td>
                    {item.periods.map((period) => (
                      <div key={`${period.period_start}-${period.period_end}`}>
                        {period.quality_count} / {formatPercent(period.quality_rate)}
                      </div>
                    ))}
                  </td>
                  <td>
                    {item.periods.map((period) => (
                      <div key={`${period.period_start}-${period.period_end}`}>
                        {period.demand_count} / {formatPercent(period.demand_rate)}
                      </div>
                    ))}
                  </td>
                  <td>
                    <div className="actions">
                      {item.report_available && (
                        <button
                          className="download secondary"
                          type="button"
                          onClick={() => onDownload(item.id)}
                          disabled={loading}
                          title="Скачать готовый Excel-отчёт"
                        >
                          Excel
                        </button>
                      )}
                      <button className="dangerButton" disabled={loading} onClick={() => onAskDelete(item.id)}>
                        Удалить
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {deletingExport !== null && (
        <div className="confirmBar">
          <span>Удалить выбранный отчёт из истории?</span>
          <div className="actions">
            <button className="dangerButton" disabled={loading} onClick={onConfirmDelete}>
              Удалить
            </button>
            <button className="ghostButton" disabled={loading} onClick={onCancelDelete}>
              Отмена
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
