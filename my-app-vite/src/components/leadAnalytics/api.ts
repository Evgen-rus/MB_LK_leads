import { downloadApiFile, http, httpForm } from "../../api";
import type {
  AnalysisPeriod,
  AnalyticsClient,
  AnalyticsGroup,
  AnalyticsProject,
  SavedExportResult,
  SavedResultFilter,
  SavedResultRows,
  SavedResultTable,
  AnalyzeSetup,
  ExportRecord,
  Mapping,
  ProcessingJob,
  StatusRuleItem,
  StatusRulesData,
  UploadResponse,
  WorkbookPreview
} from "./types";

const base = "/admin/analytics";
const json = (method: string, value?: unknown): RequestInit => ({
  method,
  ...(value === undefined ? {} : { body: JSON.stringify(value) })
});

export const fetchClients = () => http<AnalyticsClient[]>(`${base}/clients`);

export const fetchProjects = (clientId: number, query: string) =>
  http<AnalyticsProject[]>(`${base}/clients/${clientId}/projects?q=${encodeURIComponent(query)}`);

export const fetchGroups = (clientId: number) =>
  http<AnalyticsGroup[]>(`${base}/clients/${clientId}/groups`);

export const createGroup = (clientId: number, group: Pick<AnalyticsGroup, "name" | "project_ids" | "spreadsheet_url">) =>
  http<AnalyticsGroup>(`${base}/clients/${clientId}/groups`, json("POST", group));

export const updateGroup = (groupId: number, group: Pick<AnalyticsGroup, "name" | "project_ids" | "spreadsheet_url">) =>
  http<AnalyticsGroup>(`${base}/groups/${groupId}`, json("PUT", group));

export const archiveGroup = (groupId: number) =>
  http<{ archived: boolean }>(`${base}/groups/${groupId}`, json("DELETE"));

export const fetchStatusRules = (groupId: number) =>
  http<StatusRulesData>(`${base}/groups/${groupId}/status-rules`);

export const createStatusRule = (groupId: number, pattern: string, groupName: string) =>
  http<StatusRuleItem>(`${base}/groups/${groupId}/status-rules`, json("POST", { pattern, group_name: groupName }));

export const updateStatusRule = (groupId: number, ruleId: number, groupName: string) =>
  http<StatusRuleItem>(`${base}/groups/${groupId}/status-rules/${ruleId}`, json("PUT", { group_name: groupName }));

export const deleteStatusRule = (groupId: number, ruleId: number) =>
  http<{ deleted: boolean }>(`${base}/groups/${groupId}/status-rules/${ruleId}`, json("DELETE"));

export const fetchExports = (groupId: number) =>
  http<ExportRecord[]>(`${base}/groups/${groupId}/exports`);

export const fetchExportResult = (groupId: number, exportId: number) =>
  http<SavedExportResult>(`${base}/groups/${groupId}/exports/${exportId}/result`);

export function fetchExportResultRows(
  groupId: number,
  exportId: number,
  params: {
    table: SavedResultTable;
    page: number;
    pageSize: number;
    query?: string;
    filters?: Record<string, SavedResultFilter>;
    sort?: string | null;
    direction?: "asc" | "desc";
  }
) {
  const query = new URLSearchParams({
    table: params.table,
    page: String(params.page),
    page_size: String(params.pageSize),
  });
  if (params.query) query.set("query", params.query);
  if (params.filters && Object.keys(params.filters).length) query.set("filters", JSON.stringify(params.filters));
  if (params.sort) {
    query.set("sort", params.sort);
    query.set("direction", params.direction ?? "desc");
  }
  return http<SavedResultRows>(`${base}/groups/${groupId}/exports/${exportId}/result/rows?${query}`);
}

export const deleteExport = (groupId: number, exportId: number) =>
  http<{ deleted: boolean }>(`${base}/groups/${groupId}/exports/${exportId}`, json("DELETE"));

export const downloadExport = (groupId: number, exportId: number, filename: string) =>
  downloadApiFile(`${base}/groups/${groupId}/exports/${exportId}/download`, filename);

export async function prepareRun(
  groupId: number,
  periods: AnalysisPeriod[],
  clientFile: File | null,
  clientUrl: string
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("periods", JSON.stringify(periods));
  if (clientFile) form.append("client_file", clientFile);
  else form.append("client_url", clientUrl.trim());
  return httpForm<UploadResponse>(`${base}/groups/${groupId}/runs`, form, { method: "POST" });
}

export const queueMatchJob = (groupId: number, runId: string, lkMapping: Mapping, clientMapping: Mapping) =>
  http<ProcessingJob>(`${base}/groups/${groupId}/runs/${encodeURIComponent(runId)}/match/jobs`, json("POST", {
    lk_mapping: lkMapping,
    client_mapping: clientMapping
  }));

export const fetchAnalyzeSetup = (groupId: number, runId: string, mapping?: Mapping) =>
  http<AnalyzeSetup>(`${base}/groups/${groupId}/runs/${encodeURIComponent(runId)}/analyze/setup`, json("POST", mapping ? { mapping } : {}));

export const queueAnalyzeJob = (
  groupId: number,
  runId: string,
  payload: {
    mapping: Mapping;
    status_rules: Record<string, string>;
    periods: AnalysisPeriod[];
    analysis_date: string | null;
    source_file_name: string;
  }
) => http<ProcessingJob>(
  `${base}/groups/${groupId}/runs/${encodeURIComponent(runId)}/analyze/jobs`,
  json("POST", payload)
);

export const fetchProcessingJob = (groupId: number, jobId: number) =>
  http<ProcessingJob>(`${base}/groups/${groupId}/jobs/${jobId}`);

export const fetchProcessingJobPreview = (groupId: number, jobId: number) =>
  http<WorkbookPreview>(`${base}/groups/${groupId}/jobs/${jobId}/preview`);

export const downloadMatch = (groupId: number, runId: string, filename: string) =>
  downloadApiFile(`${base}/groups/${groupId}/runs/${encodeURIComponent(runId)}/match/download`, filename);
