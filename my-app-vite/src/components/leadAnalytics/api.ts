import { downloadApiFile, http, httpForm } from "../../api";
import type {
  AnalysisPeriod,
  AnalyticsClient,
  AnalyticsGroup,
  AnalyticsProject,
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

export const updateStatusRule = (groupId: number, ruleId: number, groupName: string) =>
  http<StatusRuleItem>(`${base}/groups/${groupId}/status-rules/${ruleId}`, json("PUT", { group_name: groupName }));

export const deleteStatusRule = (groupId: number, ruleId: number) =>
  http<{ deleted: boolean }>(`${base}/groups/${groupId}/status-rules/${ruleId}`, json("DELETE"));

export const fetchExports = (groupId: number) =>
  http<ExportRecord[]>(`${base}/groups/${groupId}/exports`);

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

export const fetchAnalyzeSetup = (groupId: number, runId: string) =>
  http<AnalyzeSetup>(`${base}/groups/${groupId}/runs/${encodeURIComponent(runId)}/analyze/setup`, json("POST", {}));

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
