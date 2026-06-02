export type PhoneValidationError = {
  /** 1-based номер строки в textarea */
  lineNumber: number;
  /** исходное значение строки (trim) */
  raw: string;
  /** человекочитаемая причина */
  reason: string;
};

export type NormalizePhonesResult = {
  normalized: string[];
  errors: PhoneValidationError[];
  /** Текст для textarea: валидные строки нормализованы, невалидные оставлены как есть */
  displayText: string;
};

function normalizePhoneLine(rawLine: string): { normalized?: string; reason?: string } {
  const raw = rawLine.trim();
  if (!raw) return {};
  const digits = raw.replace(/\D+/g, '');

  if (digits.length !== 11) {
    return { reason: 'нужно ровно 11 цифр' };
  }
  if (digits[0] === '8') {
    return { normalized: `7${digits.slice(1)}` };
  }
  if (digits[0] !== '7') {
    return { reason: 'номер должен начинаться с 7 или 8' };
  }
  return { normalized: digits };
}

/**
 * Нормализация/валидация телефонов из multiline textarea.
 * Правило: один номер в строке, строго 11 цифр, начинается с 7 или 8.
 * Российский формат с первой 8 приводим к формату с первой 7.
 */
export function normalizePhonesMultiline(text: string): NormalizePhonesResult {
  const lines = text.split(/\r?\n/);

  const normalized: string[] = [];
  const errors: PhoneValidationError[] = [];
  const seen = new Set<string>();

  const displayLines: string[] = [];

  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    if (!trimmed) return;

    const res = normalizePhoneLine(trimmed);
    if (res.normalized) {
      if (!seen.has(res.normalized)) {
        seen.add(res.normalized);
        normalized.push(res.normalized);
        displayLines.push(res.normalized);
      }
      return;
    }
    errors.push({
      lineNumber: idx + 1,
      raw: trimmed,
      reason: res.reason || 'некорректный формат',
    });
    displayLines.push(trimmed);
  });

  return {
    normalized,
    errors,
    displayText: displayLines.join('\n'),
  };
}


