import { useEffect, useMemo, useState } from "react";
import {
  archiveGroup,
  createGroup,
  deleteExport,
  deleteStatusRule,
  downloadExport,
  fetchAnalyzeSetup,
  fetchClients,
  fetchExports,
  fetchGroups,
  fetchProcessingJob,
  fetchProcessingJobPreview,
  fetchProjects,
  fetchStatusRules,
  prepareRun,
  queueAnalyzeJob,
  queueMatchJob,
  updateGroup,
  updateStatusRule
} from "./api";
import {
  AnalyzeSummary,
  ExportHistory,
  FileDropZone,
  MappingPanel,
  MatchSummary,
  ProcessProgress,
  StatusRulesManager,
  StatusRulesModal,
  Stepper,
  WorkbookViewer
} from "./components";
import type {
  AnalysisPeriod,
  AnalyticsClient,
  AnalyticsGroup,
  AnalyticsProject,
  AnalyzeSetup,
  ExportRecord,
  Mapping,
  OperationStage,
  ProcessingJob,
  StatusRulesData,
  Step,
  UploadResponse,
  WorkbookPreview
} from "./types";
import "./styles.css";

const emptyMapping: Mapping = { sheet_name: "" };
const emptyPeriod = (): AnalysisPeriod => ({ period_start: "", period_end: "" });
const todayIso = () => new Date().toISOString().slice(0, 10);

function normalizeMapping(mapping: Mapping, sheetName: string): Mapping {
  return { ...mapping, sheet_name: mapping.sheet_name || sheetName };
}

function periodIssue(period: AnalysisPeriod): string | null {
  if (!period.period_start || !period.period_end) return "Укажите даты начала и окончания периода.";
  if (period.period_start > period.period_end) return "Дата начала не может быть позже даты окончания.";
  return null;
}

function periodIsValid(period: AnalysisPeriod): boolean {
  return !periodIssue(period);
}

function sameIds(left: number[], right: number[]): boolean {
  return left.length === right.length && left.every((id) => right.includes(id));
}

export default function LeadAnalytics() {
  const [clients, setClients] = useState<AnalyticsClient[]>([]);
  const [clientId, setClientId] = useState<number | null>(null);
  const [groups, setGroups] = useState<AnalyticsGroup[]>([]);
  const [groupId, setGroupId] = useState<number | null>(null);
  const [creatingGroup, setCreatingGroup] = useState(true);
  const [groupName, setGroupName] = useState("");
  const [projectIds, setProjectIds] = useState<number[]>([]);
  const [spreadsheetUrl, setSpreadsheetUrl] = useState("");
  const [projects, setProjects] = useState<AnalyticsProject[]>([]);
  const [projectSearch, setProjectSearch] = useState("");
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [clientsLoading, setClientsLoading] = useState(true);

  const [savedExports, setSavedExports] = useState<ExportRecord[]>([]);
  const [lkFile, setLkFile] = useState<FileInspect | null>(null);
  const [clientFile, setClientFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [preparedPeriods, setPreparedPeriods] = useState<AnalysisPeriod[] | null>(null);
  const [lkMapping, setLkMapping] = useState<Mapping>(emptyMapping);
  const [clientMapping, setClientMapping] = useState<Mapping>(emptyMapping);
  const [matchPreview, setMatchPreview] = useState<WorkbookPreview | null>(null);
  const [analyzeSetup, setAnalyzeSetup] = useState<AnalyzeSetup | null>(null);
  const [analyzeMapping, setAnalyzeMapping] = useState<Mapping>(emptyMapping);
  const [statusRules, setStatusRules] = useState<Record<string, string>>({});
  const [rulesData, setRulesData] = useState<StatusRulesData | null>(null);
  const [analyzePreview, setAnalyzePreview] = useState<WorkbookPreview | null>(null);
  const [periods, setPeriods] = useState<AnalysisPeriod[]>([emptyPeriod()]);
  const [analysisDate, setAnalysisDate] = useState(todayIso());
  const [step, setStep] = useState<Step>("upload");
  const [loading, setLoading] = useState(false);
  const [operation, setOperation] = useState("");
  const [operationStage, setOperationStage] = useState<OperationStage | null>(null);
  const [error, setError] = useState("");
  const [deletingExport, setDeletingExport] = useState<number | null>(null);
  const [archiveConfirmation, setArchiveConfirmation] = useState(false);
  const [statusModalOpen, setStatusModalOpen] = useState(false);
  const [rulesManagerOpen, setRulesManagerOpen] = useState(false);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [activeJob, setActiveJob] = useState<ProcessingJob | null>(null);
  const [currentExportId, setCurrentExportId] = useState<number | null>(null);

  const client = clients.find((item) => item.id === clientId) ?? null;
  const selectedGroup = groups.find((item) => item.id === groupId) ?? null;
  const isArchived = Boolean(selectedGroup?.archived);
  const groupDirty = !creatingGroup && selectedGroup
    ? groupName.trim() !== selectedGroup.name ||
      !sameIds(projectIds, selectedGroup.project_ids) ||
      spreadsheetUrl.trim() !== (selectedGroup.spreadsheet_url ?? "").trim()
    : Boolean(groupName.trim());
  const filteredProjects = useMemo(() => {
    const query = projectSearch.trim().toLocaleLowerCase("ru");
    if (!query) return projects;
    return projects.filter((item) =>
      item.name.toLocaleLowerCase("ru").includes(query) ||
      (item.collection_source ?? "").toLocaleLowerCase("ru").includes(query)
    );
  }, [projectSearch, projects]);
  const validPeriods = periods.length > 0 && periods.every(periodIsValid);
  const periodsMatchPrepared = Boolean(preparedPeriods && JSON.stringify(preparedPeriods) === JSON.stringify(periods));
  const hasClientSource = Boolean(clientFile || spreadsheetUrl.trim());
  const canPrepare = Boolean(clientId && selectedGroup && !isArchived && !groupDirty && projectIds.length && hasClientSource && validPeriods && !loading);
  const canMatch = Boolean(upload && periodsMatchPrepared && !groupDirty && lkMapping.lkid_column && lkMapping.source_column && clientMapping.status_column);
  const canAnalyze = Boolean(
    upload && periodsMatchPrepared && !groupDirty && analyzeMapping.status_column && analyzeMapping.date_column && validPeriods && !loading
  );
  const nextExportNumber = Math.max(0, ...savedExports.map((item) => item.export_number ?? 0)) + 1;
  const statusText = loading
    ? operation || "Выполняется..."
    : step === "upload" ? canPrepare ? "Источники готовы к подготовке" : "Выберите клиента, группу и источники"
    : step === "mapping" ? "Проверьте колонки"
    : step === "analyze" ? "Настройте аналитику" : "Отчёт готов";

  useEffect(() => {
    let active = true;
    setClientsLoading(true);
    fetchClients()
      .then((data) => { if (active) setClients(data); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "Не удалось загрузить клиентов"); })
      .finally(() => { if (active) setClientsLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (clientId === null) return;
    let active = true;
    setGroupsLoading(true);
    setProjectsLoading(true);
    Promise.all([fetchGroups(clientId), fetchProjects(clientId, "")])
      .then(([nextGroups, nextProjects]) => {
        if (!active) return;
        setGroups(nextGroups);
        setProjects(nextProjects);
        const first = nextGroups.find((item) => !item.archived) ?? nextGroups[0];
        if (first) {
          setCreatingGroup(false);
          setGroupId(first.id);
          setGroupName(first.name);
          setProjectIds([...first.project_ids]);
          setSpreadsheetUrl(first.spreadsheet_url ?? "");
        } else {
          setCreatingGroup(true);
          setGroupId(null);
          setGroupName("");
          setProjectIds([]);
          setSpreadsheetUrl(client?.table_url ?? "");
        }
      })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "Не удалось загрузить группы и проекты"); })
      .finally(() => { if (active) { setGroupsLoading(false); setProjectsLoading(false); } });
    return () => { active = false; };
  }, [clientId, client?.table_url]);

  useEffect(() => {
    if (groupId === null) {
      setSavedExports([]);
      return;
    }
    let active = true;
    fetchExports(groupId)
      .then((items) => { if (active) setSavedExports(items); })
      .catch((err) => { if (active) { setSavedExports([]); setError(err instanceof Error ? err.message : "Не удалось загрузить историю"); } });
    return () => { active = false; };
  }, [groupId]);

  useEffect(() => {
    if (clientId === null || !projectSearch.trim()) return;
    let active = true;
    const timer = window.setTimeout(() => {
      fetchProjects(clientId, projectSearch)
        .then((rows) => {
          if (!active) return;
          setProjects((current) => {
            const byId = new Map(current.map((item) => [item.id, item]));
            rows.forEach((item) => byId.set(item.id, item));
            return [...byId.values()];
          });
        })
        .catch((err) => { if (active) setError(err instanceof Error ? err.message : "Не удалось найти проекты"); });
    }, 200);
    return () => { active = false; window.clearTimeout(timer); };
  }, [clientId, projectSearch]);

  function resetRun() {
    setUpload(null);
    setPreparedPeriods(null);
    setLkFile(null);
    setClientFile(null);
    setLkMapping(emptyMapping);
    setClientMapping(emptyMapping);
    setMatchPreview(null);
    setAnalyzeSetup(null);
    setAnalyzeMapping(emptyMapping);
    setStatusRules({});
    setRulesData(null);
    setAnalyzePreview(null);
    setPeriods([emptyPeriod()]);
    setAnalysisDate(todayIso());
    setActiveJob(null);
    setCurrentExportId(null);
    setStatusModalOpen(false);
    setRulesManagerOpen(false);
    setStep("upload");
    setError("");
  }

  function selectClient(nextId: number | null) {
    setClientId(nextId);
    setGroups([]);
    setProjects([]);
    setGroupId(null);
    setCreatingGroup(true);
    setGroupName("");
    setProjectIds([]);
    setProjectSearch("");
    setSpreadsheetUrl("");
    setArchiveConfirmation(false);
    setSavedExports([]);
    resetRun();
  }

  function selectGroup(next: AnalyticsGroup | null) {
    setGroupId(next?.id ?? null);
    setCreatingGroup(!next);
    setGroupName(next?.name ?? "");
    setProjectIds(next ? [...next.project_ids] : []);
    setSpreadsheetUrl(next?.spreadsheet_url ?? client?.table_url ?? "");
    setArchiveConfirmation(false);
    resetRun();
  }

  function toggleProject(id: number) {
    setProjectIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function toggleVisibleProjects(checked: boolean) {
    const visible = filteredProjects.map((item) => item.id);
    setProjectIds((current) => checked
      ? [...new Set([...current, ...visible])]
      : current.filter((id) => !visible.includes(id))
    );
  }

  async function saveGroup() {
    if (clientId === null || !groupName.trim() || projectIds.length === 0) return;
    setLoading(true);
    setOperation(creatingGroup ? "Создаю группу аналитики" : "Сохраняю группу аналитики");
    setOperationStage("upload");
    setError("");
    try {
      const payload = { name: groupName.trim(), project_ids: projectIds, spreadsheet_url: spreadsheetUrl.trim() };
      const saved = creatingGroup || groupId === null
        ? await createGroup(clientId, payload)
        : await updateGroup(groupId, payload);
      setGroups((current) => {
        const exists = current.some((item) => item.id === saved.id);
        return exists ? current.map((item) => item.id === saved.id ? saved : item) : [...current, saved];
      });
      setGroupId(saved.id);
      setCreatingGroup(false);
      setGroupName(saved.name);
      setProjectIds([...saved.project_ids]);
      setSpreadsheetUrl(saved.spreadsheet_url ?? "");
      resetRun();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить группу");
    } finally {
      setLoading(false);
      setOperation("");
      setOperationStage(null);
    }
  }

  async function archiveSelectedGroup() {
    if (!selectedGroup) return;
    setLoading(true);
    setOperation("Расформировываю группу");
    setError("");
    try {
      await archiveGroup(selectedGroup.id);
      setGroups((current) => current.map((item) => item.id === selectedGroup.id ? { ...item, archived: true } : item));
      setArchiveConfirmation(false);
      resetRun();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось расформировать группу");
    } finally {
      setLoading(false);
      setOperation("");
    }
  }

  async function prepareData() {
    if (!canPrepare || !selectedGroup) return;
    setLoading(true);
    setOperation("Подготавливаю идентификации и читаю источник клиента");
    setOperationStage("upload");
    setError("");
    try {
      const data = await prepareRun(selectedGroup.id, periods, clientFile, spreadsheetUrl);
      setUpload(data);
      setPreparedPeriods(periods.map((period) => ({ ...period })));
      setLkFile(data.lk);
      setLkMapping(normalizeMapping(data.lk.detected, data.lk.sheets[0]?.name ?? ""));
      setClientMapping(normalizeMapping(data.client.detected, data.client.sheets[0]?.name ?? ""));
      setMatchPreview(null);
      setAnalyzeSetup(null);
      setAnalyzePreview(null);
      setStep("mapping");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось подготовить данные");
    } finally {
      setLoading(false);
      setOperation("");
      setOperationStage(null);
    }
  }

  async function waitForJob(initial: ProcessingJob): Promise<ProcessingJob> {
    let current = initial;
    setActiveJob(current);
    while (current.status === "queued" || current.status === "running") {
      setOperation(current.phase || (current.status === "queued" ? "Ожидает в очереди" : "Выполняется"));
      setOperationStage(current.kind === "match" ? "match" : "prepare");
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      if (!selectedGroup) throw new Error("Выберите группу аналитики снова");
      current = await fetchProcessingJob(selectedGroup.id, current.id);
      setActiveJob(current);
    }
    if (current.status === "failed") throw new Error(current.error_text || "Задание завершилось с ошибкой");
    setActiveJob(null);
    return current;
  }

  async function runMatch() {
    if (!upload || !selectedGroup || !canMatch) return;
    setLoading(true);
    setOperation("Сопоставляю строки и проверяю колонки");
    setOperationStage("match");
    setError("");
    try {
      const job = await queueMatchJob(selectedGroup.id, upload.run_id, lkMapping, clientMapping);
      const completed = await waitForJob(job);
      setMatchPreview(await fetchProcessingJobPreview(selectedGroup.id, completed.id));
      setOperation("Определяю колонки аналитики и неизвестные статусы");
      setOperationStage("prepare");
      const [setup, savedRules] = await Promise.all([
        fetchAnalyzeSetup(selectedGroup.id, upload.run_id),
        fetchStatusRules(selectedGroup.id)
      ]);
      setAnalyzeSetup(setup);
      setAnalyzeMapping(normalizeMapping(setup.mapping, setup.mapping.sheet_name || setup.sheets[0]?.name || ""));
      setRulesData(savedRules);
      setStatusRules({});
      setStep("analyze");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сопоставить данные");
    } finally {
      setLoading(false);
      setOperation("");
      setOperationStage(null);
    }
  }

  function requestAnalyze() {
    if (!analyzeSetup) return;
    if (analyzeSetup.unknown_statuses.length > 0) {
      setStatusModalOpen(true);
      return;
    }
    void runAnalyze();
  }

  async function runAnalyze() {
    if (!upload || !selectedGroup || !canAnalyze) return;
    const missing = analyzeSetup?.unknown_statuses.filter((status) => !statusRules[status]);
    if (missing?.length) {
      setStatusModalOpen(true);
      setError("Выберите группу для каждого неизвестного статуса перед аналитикой");
      return;
    }
    setStatusModalOpen(false);
    setLoading(true);
    setOperation("Формирую аналитику и сохраняю отчёт");
    setOperationStage("prepare");
    setError("");
    try {
      const job = await queueAnalyzeJob(selectedGroup.id, upload.run_id, {
        mapping: analyzeMapping,
        status_rules: statusRules,
        periods,
        analysis_date: analysisDate || null,
        source_file_name: clientFile?.name || upload.client.filename
      });
      const completed = await waitForJob(job);
      setOperationStage("done");
      setAnalyzePreview(await fetchProcessingJobPreview(selectedGroup.id, completed.id));
      const refreshed = await fetchExports(selectedGroup.id);
      setSavedExports(refreshed);
      setCurrentExportId(completed.export_id ?? null);
      setStep("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сформировать аналитику");
    } finally {
      setLoading(false);
      setOperation("");
      setOperationStage(null);
    }
  }

  async function confirmDeleteExport() {
    if (!selectedGroup || deletingExport === null) return;
    setLoading(true);
    setOperation("Удаляю отчёт из истории");
    setError("");
    try {
      await deleteExport(selectedGroup.id, deletingExport);
      setDeletingExport(null);
      const refreshed = await fetchExports(selectedGroup.id);
      setSavedExports(refreshed);
      if (currentExportId === deletingExport) setCurrentExportId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить отчёт");
    } finally {
      setLoading(false);
      setOperation("");
    }
  }

  async function downloadReport(exportId: number) {
    if (!selectedGroup) return;
    setLoading(true);
    setError("");
    try {
      await downloadExport(selectedGroup.id, exportId, `analytics-${exportId}.xlsx`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось скачать Excel-отчёт");
    } finally {
      setLoading(false);
    }
  }

  async function openRulesManager() {
    if (!selectedGroup || isArchived) return;
    setRulesManagerOpen(true);
    setRulesLoading(true);
    setError("");
    try {
      setRulesData(await fetchStatusRules(selectedGroup.id));
    } catch (err) {
      setRulesManagerOpen(false);
      setError(err instanceof Error ? err.message : "Не удалось загрузить соответствия статусов");
    } finally {
      setRulesLoading(false);
    }
  }

  async function saveStatusRule(ruleId: number, groupNameValue: string) {
    if (!selectedGroup) return;
    setRulesLoading(true);
    setError("");
    try {
      const updated = await updateStatusRule(selectedGroup.id, ruleId, groupNameValue);
      setRulesData((current) => current ? {
        ...current,
        project_rules: current.project_rules.map((rule) => rule.id === ruleId ? updated : rule)
      } : current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить соответствие");
    } finally {
      setRulesLoading(false);
    }
  }

  async function removeStatusRule(ruleId: number) {
    if (!selectedGroup) return;
    setRulesLoading(true);
    setError("");
    try {
      await deleteStatusRule(selectedGroup.id, ruleId);
      setRulesData((current) => current ? {
        ...current,
        project_rules: current.project_rules.filter((rule) => rule.id !== ruleId)
      } : current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить соответствие");
    } finally {
      setRulesLoading(false);
    }
  }

  const clientUrlSuggestions = [
    { label: "Обычная таблица", url: client?.table_url },
    { label: "Pixel-таблица", url: client?.pixel_table_url }
  ].filter((item): item is { label: string; url: string } => Boolean(item.url));

  return (
    <section className="lead-analytics" aria-label="Аналитика">
      <section className="topbar">
        <div>
          <h1>Аналитика</h1>
          <p>Подготовка данных, сопоставление и отчёты по группам проектов</p>
        </div>
        <div className={`statusBadge ${loading ? "busy" : step === "upload" && !canPrepare ? "needsInput" : "ready"}`}>
          <span aria-hidden="true" />{statusText}
        </div>
      </section>

      <section className="panel groupPanel">
        <div className="panelHeader">
          <div>
            <h2>Клиент и группа проектов</h2>
            <p>Группы аналитики настраиваются отдельно от дневных лимитов. Проект может входить в несколько групп.</p>
          </div>
          <div className="actions">
            <button className="ghostButton" type="button" onClick={openRulesManager} disabled={!selectedGroup || isArchived || loading}>
              Соответствия статусов
            </button>
          </div>
        </div>
        <div className="twoColumn">
          <label className="field">
            <span>Клиент</span>
            <select value={clientId ?? ""} disabled={clientsLoading || loading} onChange={(event) => selectClient(event.target.value ? Number(event.target.value) : null)}>
              <option value="">Выберите клиента</option>
              {clients.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Группа</span>
            <select
              value={creatingGroup ? "new" : groupId ?? ""}
              disabled={!clientId || groupsLoading || loading}
              onChange={(event) => {
                if (event.target.value === "new") selectGroup(null);
                else selectGroup(groups.find((item) => item.id === Number(event.target.value)) ?? null);
              }}
            >
              <option value="">Выберите группу</option>
              {groups.map((item) => <option key={item.id} value={item.id}>{item.name}{item.archived ? " · архив" : ""}</option>)}
              <option value="new">＋ Новая группа</option>
            </select>
          </label>
        </div>

        {clientId !== null && (
          <>
            <div className="twoColumn groupFields">
              <label className="field">
                <span>Название группы</span>
                <input value={groupName} disabled={(!creatingGroup && isArchived) || loading || step !== "upload"} onChange={(event) => setGroupName(event.target.value)} placeholder="Например, Все сайты и звонки" />
              </label>
              <label className="field">
                <span>Источник данных клиента · Google-таблица</span>
                <input type="url" value={spreadsheetUrl} disabled={(!creatingGroup && isArchived) || loading || step !== "upload"} onChange={(event) => setSpreadsheetUrl(event.target.value)} placeholder="https://docs.google.com/spreadsheets/d/..." />
              </label>
            </div>
            {clientUrlSuggestions.length > 0 && (
              <div className="suggestionRow" aria-label="Ссылки клиента">
                <span>Подставить:</span>
                {clientUrlSuggestions.map((item) => (
                  <button className="ghostButton" type="button" key={item.label} disabled={isArchived || loading || step !== "upload"} onClick={() => setSpreadsheetUrl(item.url)}>{item.label}</button>
                ))}
              </div>
            )}
            <div className="groupProjectsHeader">
              <div>
                <h3>Проекты в группе</h3>
                <p>{projectIds.length} включено · новые проекты выбираются вручную</p>
              </div>
              <label className="columnSearch">
                <span className="visuallyHidden">Поиск проектов</span>
                <input value={projectSearch} disabled={isArchived || loading || step !== "upload"} onChange={(event) => setProjectSearch(event.target.value)} placeholder="Найти проект..." />
              </label>
            </div>
            {projectsLoading ? <p>Загружаю проекты…</p> : filteredProjects.length === 0 ? (
              <div className="emptyState"><strong>Проекты не найдены</strong><span>Измените запрос поиска.</span></div>
            ) : (
              <>
                <div className="projectSelectionActions">
                  <span>{filteredProjects.length} показано · {projectIds.filter((id) => filteredProjects.some((item) => item.id === id)).length} включено</span>
                  <button className="ghostButton" type="button" disabled={isArchived || loading || step !== "upload"} onClick={() => toggleVisibleProjects(filteredProjects.some((item) => !projectIds.includes(item.id)))}>
                    {filteredProjects.some((item) => !projectIds.includes(item.id)) ? "Добавить все показанные" : "Исключить показанные"}
                  </button>
                </div>
                <div className="projectSelectionList">
                  {filteredProjects.map((item) => {
                    const included = projectIds.includes(item.id);
                    const removed = Boolean(item.deleted_at);
                    return (
                      <label className={`projectChoice ${included ? "included" : ""}`} key={item.id}>
                        <input type="checkbox" checked={included} disabled={isArchived || loading || step !== "upload"} onChange={() => toggleProject(item.id)} />
                        <span className="projectChoiceName">{item.name}</span>
                        <span className="projectChoiceMeta">{included ? "В группе" : "Вне группы"} · {item.collection_source || "источник не указан"}{removed ? " · удалён, история сохранена" : ""}</span>
                      </label>
                    );
                  })}
                </div>
              </>
            )}
            {!projectsLoading && projectIds.some((id) => !projects.some((item) => item.id === id)) && (
              <div className="missingProjects">
                <strong>Сохранённые проекты, которых нет в каталоге</strong>
                {projectIds.filter((id) => !projects.some((item) => item.id === id)).map((id) => (
                  <div className="projectChoice" key={id}>
                    <input type="checkbox" checked disabled aria-label={`Проект ${id} сохранён в группе`} />
                    <span className="projectChoiceName">Проект #{id}</span>
                    <span className="projectChoiceMeta">Проект удалён из каталога</span>
                    <button type="button" className="ghostButton" disabled={isArchived || loading || step !== "upload"} onClick={() => toggleProject(id)}>Убрать</button>
                  </div>
                ))}
              </div>
            )}
            <div className="groupActions">
              {creatingGroup || groupDirty ? (
                <button type="button" onClick={saveGroup} disabled={!groupName.trim() || projectIds.length === 0 || loading || step !== "upload"}>
                  {creatingGroup ? "Создать группу" : "Сохранить группу"}
                </button>
              ) : <span className="savedHint">Изменений нет</span>}
              {!creatingGroup && selectedGroup && !isArchived && (
                archiveConfirmation ? (
                  <div className="inlineConfirm">
                    <span>Расформировать группу? История отчётов останется доступна.</span>
                    <button className="dangerButton" type="button" disabled={loading} onClick={() => void archiveSelectedGroup()}>Расформировать</button>
                    <button className="ghostButton" type="button" disabled={loading} onClick={() => setArchiveConfirmation(false)}>Отмена</button>
                  </div>
                ) : <button className="dangerButton" type="button" disabled={loading} onClick={() => setArchiveConfirmation(true)}>Расформировать группу</button>
              )}
              {isArchived && <span className="archivedHint">Группа расформирована. История доступна, новые отчёты создавать нельзя.</span>}
            </div>
          </>
        )}
      </section>

      <Stepper step={step} />
      <ProcessProgress active={loading} stage={activeJob ? activeJob.kind === "match" ? "match" : "prepare" : operationStage} label={activeJob?.phase || operation} processedRows={activeJob?.processed_rows} totalRows={activeJob?.total_rows} queued={activeJob?.status === "queued"} />
      {error && <div className="alert" role="alert">{error}</div>}

      {step === "upload" && (
        <>
          {selectedGroup && !isArchived && (
            <section className="panel sourcePanel">
              <div className="panelHeader compact">
                <div>
                  <h2>Периоды и источник данных клиента</h2>
                  <p>Идентификации из ЛК подготовятся автоматически для проектов группы.</p>
                </div>
              </div>
              <div className="periodList">
                {periods.map((period, index) => {
                  const issue = periodIssue(period);
                  const invalidOrder = Boolean(period.period_start && period.period_end && period.period_start > period.period_end);
                  return (
                    <div className="periodBlock" key={index}>
                      <div className="periodRow">
                        <strong>Период {index + 1}</strong>
                        <label className="field"><span>От *</span><input type="date" value={period.period_start} aria-invalid={invalidOrder || !period.period_start} onChange={(event) => setPeriods((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, period_start: event.target.value } : item))} /></label>
                        <label className="field"><span>До *</span><input type="date" value={period.period_end} aria-invalid={invalidOrder || !period.period_end} onChange={(event) => setPeriods((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, period_end: event.target.value } : item))} /></label>
                        {periods.length > 1 && <button className="dangerButton" type="button" onClick={() => setPeriods((current) => current.filter((_, itemIndex) => itemIndex !== index))}>Удалить</button>}
                      </div>
                      {issue && <p className={`periodMessage ${invalidOrder ? "fieldError" : ""}`}>{issue}</p>}
                    </div>
                  );
                })}
                <button className="ghostButton" type="button" disabled={loading} onClick={() => setPeriods((current) => [...current, emptyPeriod()])}>Добавить период</button>
              </div>
              <FileDropZone label="Файл клиента или сохранённая Google-таблица" file={clientFile} spreadsheetUrl={spreadsheetUrl} disabled={loading || isArchived} onChange={setClientFile} onSpreadsheetUrlChange={setSpreadsheetUrl} />
              <p className="uploadHint">Ссылка сохранится в настройках группы. Для конкретного запуска можно загрузить XLSX-файл.</p>
              <div className="actions prepareActions">
                <button type="button" onClick={() => void prepareData()} disabled={!canPrepare}>Подготовить данные</button>
                {groupDirty && <span className="runReviewHint">Сначала сохраните изменения группы.</span>}
                {!projectIds.length && <span className="runReviewHint">Добавьте в группу хотя бы один проект.</span>}
                {!validPeriods && <span className="runReviewHint">Укажите корректные периоды.</span>}
              </div>
            </section>
          )}
          {selectedGroup && isArchived && (
            <div className="emptyState"><strong>Группа в архиве</strong><span>Старые отчёты и загрузка остаются доступными ниже.</span></div>
          )}
        </>
      )}

      {step === "mapping" && upload && (
        <>
          <section className="panel">
            <div className="panelHeader">
              <div>
                <h2>Проверка колонок</h2>
                <p>Подготовлено идентификаций ЛК: {upload.lk_row_count ?? "данные загружены"}. Источник клиента: {clientFile?.name || upload.client.filename}</p>
                {upload.project_names?.length ? <p>Проекты: {upload.project_names.join(", ")}</p> : null}
              </div>
              <div className="actions">
                <button className="ghostButton" type="button" onClick={() => setStep("upload")} disabled={loading}>Назад</button>
                <button type="button" onClick={() => void runMatch()} disabled={!canMatch || loading}>Сопоставить</button>
              </div>
            </div>
          </section>
          <div className="twoColumn">
            {lkFile && <MappingPanel title="Идентификации ЛК" file={lkFile} mapping={lkMapping} role="lk" onChange={setLkMapping} />}
            <MappingPanel title="Клиент" file={upload.client} mapping={clientMapping} role="client" onChange={setClientMapping} />
          </div>
        </>
      )}

      {step === "analyze" && upload && analyzeSetup && (
        <>
          <div className="sectionBar"><div><span>Параметры аналитики</span><p>Сопоставление готово, проверьте периоды и колонки.</p></div><button className="ghostButton" type="button" onClick={() => setStep("mapping")} disabled={loading}>Назад</button></div>
          {matchPreview && <section className="panel"><div className="panelHeader compact"><div><h2>Итог сопоставления</h2><p>{matchPreview.filename}</p></div></div><MatchSummary workbook={matchPreview} /></section>}
          <section className="panel">
            <div className="panelHeader compact"><div><h2>Периоды анализа</h2><p>Периоды сохранены вместе с этим запуском.</p></div></div>
            <div className="periodList">
              {periods.map((period, index) => <div className="periodRow" key={index}><strong>Период {index + 1}</strong><span>{period.period_start} — {period.period_end}</span></div>)}
            </div>
            <div className="exportMetaGrid">
              <label className="field"><span>Номер аналитики</span><input type="number" value={nextExportNumber} readOnly /></label>
              <label className="field"><span>Дата анализа</span><input type="date" value={analysisDate} onChange={(event) => setAnalysisDate(event.target.value)} /></label>
            </div>
          </section>
          <MappingPanel title="Колонки аналитики" file={analyzeSetup} mapping={analyzeMapping} role="analyze" onChange={setAnalyzeMapping} />
          {matchPreview && <details className="previewDisclosure"><summary>Посмотреть все листы сопоставления</summary><WorkbookViewer title="Предпросмотр сопоставления" workbook={matchPreview} /></details>}
          <section className="panel runReview">
            <div>
              <h2>Проверить и запустить</h2>
              <p>Аналитика №{nextExportNumber} · дата {analysisDate || "не указана"} · {periods.map((period) => period.period_start && period.period_end ? `${period.period_start} — ${period.period_end}` : "период не заполнен").join(" · ")}</p>
              {!canAnalyze && !loading && <p className="runReviewHint">{!periodsMatchPrepared ? "Периоды изменились: вернитесь и подготовьте данные заново." : "Проверьте обязательные колонки и даты периодов."}</p>}
              {analyzeSetup.unknown_statuses.length > 0 && <p>{analyzeSetup.unknown_statuses.length} неизвестных статусов потребуют ручного распределения.</p>}
            </div>
            <button type="button" onClick={requestAnalyze} disabled={!canAnalyze}>Сделать аналитику</button>
          </section>
          <StatusRulesModal open={statusModalOpen} setup={analyzeSetup} statusRules={statusRules} loading={loading} onChange={setStatusRules} onCancel={() => setStatusModalOpen(false)} onConfirm={() => void runAnalyze()} />
        </>
      )}

      {step === "done" && analyzePreview && (
        <>
          <section className="panel">
            <div className="panelHeader">
              <div><h2>Аналитика готова</h2><p>{analyzePreview.filename}</p></div>
              <div className="actions">
                <button type="button" disabled={!currentExportId || loading} onClick={() => currentExportId && void downloadReport(currentExportId)}>Скачать аналитику</button>
                <button className="ghostButton" type="button" onClick={resetRun} disabled={loading}>Новая аналитика</button>
              </div>
            </div>
            <AnalyzeSummary workbook={analyzePreview} />
          </section>
          <WorkbookViewer title="Предпросмотр аналитики" workbook={analyzePreview} />
        </>
      )}

      <ExportHistory
        groupName={selectedGroup?.name ?? ""}
        exports={savedExports}
        loading={loading}
        deletingExport={deletingExport}
        onAskDelete={setDeletingExport}
        onCancelDelete={() => setDeletingExport(null)}
        onConfirmDelete={() => void confirmDeleteExport()}
        onDownload={(exportId) => void downloadReport(exportId)}
      />
      <StatusRulesManager open={rulesManagerOpen} project={selectedGroup?.name ?? ""} data={rulesData} loading={rulesLoading} onClose={() => setRulesManagerOpen(false)} onSave={saveStatusRule} onDelete={removeStatusRule} />
    </section>
  );
}

type FileInspect = UploadResponse["lk"];
