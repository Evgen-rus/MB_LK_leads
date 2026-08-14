export const BULK_CREATE_MAX_LINES = 50;

export type BulkProjectLine = {
  lineNumber: number;
  raw: string;
  /** Вся строка целиком — это название проекта, включая телефон в конце. */
  name: string;
  /** Телефон из конца строки, приведённый к 7XXXXXXXXXX. */
  phone: string;
};

export type BulkProjectParseError = {
  lineNumber: number;
  raw: string;
  reason: string;
};

export type BulkProjectParseResult = {
  lines: BulkProjectLine[];
  errors: BulkProjectParseError[];
  totalNonEmpty: number;
};

const TEMPLATE_HINT = 'нужен шаблон: название_телефон, в конце 11 цифр через нижнее подчёркивание';

function normalizeTrailingPhone(phoneRaw: string): { phone?: string; reason?: string } {
  const digits = phoneRaw.replace(/\D+/g, '');
  if (phoneRaw.trim() !== digits || digits.length !== 11) {
    return { reason: TEMPLATE_HINT };
  }
  if (digits[0] === '8') {
    return { phone: `7${digits.slice(1)}` };
  }
  if (digits[0] !== '7') {
    return { reason: 'телефон в конце должен начинаться с 7 или 8' };
  }
  return { phone: digits };
}

/**
 * Разбор списка мультиназваний.
 * Одна непустая строка = один проект.
 * Шаблон: всё до последнего `_` + `_` + 11 цифр. Вся строка остаётся названием,
 * а последние 11 цифр идут в звонки этого проекта.
 */
export function parseBulkProjectLines(text: string): BulkProjectParseResult {
  const rawLines = text.split(/\r?\n/);
  const lines: BulkProjectLine[] = [];
  const errors: BulkProjectParseError[] = [];
  const seenNames = new Set<string>();
  let totalNonEmpty = 0;

  rawLines.forEach((rawLine, idx) => {
    const raw = rawLine.trim();
    if (!raw) return;

    totalNonEmpty += 1;
    const lineNumber = idx + 1;

    if (totalNonEmpty > BULK_CREATE_MAX_LINES) {
      return;
    }

    const lastUnderscore = raw.lastIndexOf('_');
    if (lastUnderscore <= 0) {
      errors.push({ lineNumber, raw, reason: TEMPLATE_HINT });
      return;
    }

    const namePart = raw.slice(0, lastUnderscore).trim();
    const phoneRaw = raw.slice(lastUnderscore + 1).trim();
    if (!namePart) {
      errors.push({ lineNumber, raw, reason: 'перед телефоном должно быть название' });
      return;
    }

    const phoneResult = normalizeTrailingPhone(phoneRaw);
    if (!phoneResult.phone) {
      errors.push({ lineNumber, raw, reason: phoneResult.reason || TEMPLATE_HINT });
      return;
    }

    if (seenNames.has(raw)) {
      errors.push({ lineNumber, raw, reason: 'такая строка уже есть в списке' });
      return;
    }
    seenNames.add(raw);

    lines.push({
      lineNumber,
      raw,
      name: raw,
      phone: phoneResult.phone,
    });
  });

  if (totalNonEmpty > BULK_CREATE_MAX_LINES) {
    errors.unshift({
      lineNumber: 0,
      raw: '',
      reason: `За один раз можно создать не больше ${BULK_CREATE_MAX_LINES} строк. Сейчас: ${totalNonEmpty}.`,
    });
  }

  return { lines, errors, totalNonEmpty };
}
