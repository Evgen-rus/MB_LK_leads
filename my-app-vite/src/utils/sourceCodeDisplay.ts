const RAW_TO_DISPLAY = {
  B1: 'A',
  B2: 'B',
  B3: 'C',
  B4: 'D',
} as const;

const DISPLAY_TO_RAW = {
  A: 'B1',
  B: 'B2',
  C: 'B3',
  D: 'B4',
} as const;

export const RAW_SOURCE_CODES = ['B1', 'B2', 'B3', 'B4'] as const;
export type RawSourceCode = (typeof RAW_SOURCE_CODES)[number];

export const DISPLAY_SOURCE_CODES = ['A', 'B', 'C', 'D'] as const;
export type DisplaySourceCode = (typeof DISPLAY_SOURCE_CODES)[number];

function normalizeCode(value: string | null | undefined): string {
  return String(value || '').trim().toUpperCase();
}

export function toDisplaySourceCode(rawCode: string | null | undefined): string {
  const normalized = normalizeCode(rawCode);
  return RAW_TO_DISPLAY[normalized as RawSourceCode] || normalized;
}

export function toRawSourceCode(displayCode: string | null | undefined): string {
  const normalized = normalizeCode(displayCode);
  return DISPLAY_TO_RAW[normalized as DisplaySourceCode] || normalized;
}

export function formatSourceTextForDisplay(text: string | null | undefined): string {
  if (!text) return '';
  return text.replace(/\b(B1|B2|B3|B4)\b/g, (match) => toDisplaySourceCode(match));
}

export function formatProjectNameForDisplay(rawName: string | null | undefined): string {
  if (!rawName) return '';
  return rawName.replace(/^(B1|B2|B3|B4)(?=[\s_-])/i, (match) => toDisplaySourceCode(match));
}

export function formatProjectNameForSubmit(displayName: string | null | undefined): string {
  if (!displayName) return '';
  return displayName.replace(/^(A|B|C|D)(?=[\s_-])/i, (match) => toRawSourceCode(match));
}

export function getDisplayProjectPrefix(
  rawCode: string | null | undefined,
  options?: { projectId?: number | null; uniqueNameApplied?: boolean },
): string {
  const displayCode = toDisplaySourceCode(rawCode);
  if (options?.uniqueNameApplied && options.projectId != null) {
    return `${displayCode}_[MB${options.projectId}] `;
  }
  return `${displayCode}_`;
}

export function getRawProjectPrefix(
  rawCode: string | null | undefined,
  options?: { projectId?: number | null; uniqueNameApplied?: boolean },
): string {
  const normalizedRawCode = toRawSourceCode(rawCode);
  if (options?.uniqueNameApplied && options.projectId != null) {
    return `${normalizedRawCode}_[MB${options.projectId}] `;
  }
  return `${normalizedRawCode}_`;
}

export function getSourceCodeFilterOptions(rawCodes: readonly string[]): Array<{ value: string; label: string }> {
  return rawCodes.map((code) => ({
    value: code,
    label: toDisplaySourceCode(code),
  }));
}
