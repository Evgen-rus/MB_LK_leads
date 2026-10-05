export type Mapping = {
  sheet_name: string;
  date_column?: string | null;
  phone_column?: string | null;
  channel_column?: string | null;
  source_column?: string | null;
  status_column?: string | null;
  comment_column?: string | null;
  lkid_column?: string | null;
  project_column?: string | null;
};

export type SheetPreview = {
  name: string;
  columns: string[];
  rows: Record<string, unknown>[];
};

export type FileInspect = {
  filename: string;
  detected: Mapping;
  sheets: SheetPreview[];
};

export type UploadResponse = {
  run_id: string;
  project: string;
  lk: FileInspect;
  client: FileInspect;
  lk_row_count?: number;
  project_names?: string[];
};

export type AnalyticsClient = {
  id: number;
  name: string;
  workStatus?: string | null;
  table_url?: string | null;
  pixel_table_url?: string | null;
};

export type AnalyticsProject = {
  id: number;
  name: string;
  status?: string | null;
  deleted_at?: string | null;
  collection_source?: string | null;
};

export type AnalyticsGroup = {
  id: number;
  client_id: number;
  name: string;
  project_ids: number[];
  spreadsheet_url?: string | null;
  archived?: boolean;
};

export type WorkbookPreview = {
  filename: string;
  sheets: SheetPreview[];
};

export type GoogleSheetsExport = {
  spreadsheet_url: string;
  spreadsheet_title: string;
  sheet_titles: string[];
};

export type ExportRecord = {
  id: number;
  export_number: number;
  report_available: boolean;
  period_start: string;
  period_end: string;
  analysis_date: string;
  source_file_name: string;
  total_count: number;
  missed_count: number;
  missed_rate: number;
  quality_count: number;
  quality_rate: number;
  demand_count: number;
  demand_rate: number;
  periods: ExportPeriodRecord[];
};

export type AnalysisPeriod = {
  period_start: string;
  period_end: string;
};

export type ExportPeriodRecord = AnalysisPeriod & {
  total_count: number;
  missed_count: number;
  missed_rate: number;
  quality_count: number;
  quality_rate: number;
  demand_count: number;
  demand_rate: number;
};

export type AnalyzeSetup = {
  filename: string;
  mapping: Mapping;
  sheets: SheetPreview[];
  unknown_statuses: string[];
  unknown_status_counts: Record<string, number>;
  status_groups: string[];
};

export type StatusRuleItem = {
  id: number | null;
  pattern: string;
  match_type: string;
  group_name: string;
  priority: number;
  source: "project" | "client" | "global" | "default";
};

export type StatusRuleConflict = {
  pattern: string;
  group_names: string[];
};

export type StatusRulesData = {
  project_rules: StatusRuleItem[];
  system_rules: StatusRuleItem[];
  status_groups: string[];
  conflicts?: StatusRuleConflict[];
};

export type Step = "upload" | "mapping" | "analyze" | "done";

export type OperationStage = "upload" | "read" | "match" | "prepare" | "done";

export type ProcessingJob = {
  id: number;
  run_id: string;
  export_id?: number | null;
  kind: "match" | "analyze";
  status: "queued" | "running" | "completed" | "failed";
  phase: string;
  processed_rows: number;
  total_rows: number;
  error_text: string | null;
  output_file_name: string | null;
};
