import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchActiveProjectOperation,
  fetchProjectOperation,
  type ProjectOperation,
} from '../api';

const DEFAULT_POLL_INTERVAL_MS = 5_000;

export function isProjectOperationActive(operation: ProjectOperation | null): boolean {
  if (!operation) return false;
  return operation.status === 'queued'
    || operation.status === 'running'
    || operation.status === 'waiting_retry';
}

/**
 * ID проектов, которые входят в активную массовую операцию.
 * По ним UI показывает ту же крутилку статуса, что и у одиночного сохранения.
 */
export function getProjectOperationBusyIds(operation: ProjectOperation | null): Set<number> {
  if (!operation || !isProjectOperationActive(operation)) return new Set();
  return new Set((operation.items ?? []).map((item) => item.projectId));
}

export function getProjectOperationStatusLabel(status: string): string {
  if (status === 'queued') return 'В очереди';
  if (status === 'running') return 'Выполняется';
  if (status === 'waiting_retry') return 'Ожидает восстановления сервиса';
  if (status === 'completed' || status === 'succeeded') return 'Завершено';
  if (status === 'needs_attention' || status === 'failed') return 'Требует внимания';
  return 'Обрабатывается';
}

function sanitizeOperationText(value: string): string {
  return value.replace(/prostats/gi, 'сервис обработки данных');
}

export function getProjectOperationUserMessage(
  operation: ProjectOperation,
  options?: { isAdmin?: boolean },
): string {
  const isAdmin = options?.isAdmin === true;
  const base = sanitizeOperationText(operation.message || '').trim();
  const fallback = operation.status === 'waiting_retry'
    ? 'Сервис обработки данных временно недоступен. Операция сохранена и продолжится автоматически.'
    : 'Операция сохранена и выполняется автоматически.';
  const lines = [base || fallback];
  if (isAdmin && operation.technicalError) {
    lines.push(`Техническая причина: ${operation.technicalError}`);
  }
  return lines.join('\n');
}

export type UseProjectOperationOptions = {
  clientId?: number;
  enabled?: boolean;
  pollIntervalMs?: number;
  onTerminal?: (operation: ProjectOperation) => void;
};

export type UseProjectOperationResult = {
  operation: ProjectOperation | null;
  loading: boolean;
  error: Error | null;
  start: (operation: ProjectOperation) => void;
  refresh: () => Promise<ProjectOperation | null>;
  clear: () => void;
};

/**
 * Tracks one durable project operation. The initial active operation is loaded
 * from the server, so closing and reopening the browser does not lose progress.
 */
export function useProjectOperation({
  clientId,
  enabled = true,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  onTerminal,
}: UseProjectOperationOptions = {}): UseProjectOperationResult {
  const [operation, setOperation] = useState<ProjectOperation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const operationRef = useRef<ProjectOperation | null>(null);
  const terminalHandledRef = useRef<number | null>(null);
  const scopeGenerationRef = useRef(0);
  const pollingErrorNotifiedRef = useRef(false);
  const onTerminalRef = useRef(onTerminal);
  onTerminalRef.current = onTerminal;

  const updateOperation = useCallback((next: ProjectOperation | null) => {
    // Завершённую операцию в state не держим: баннер должен сразу пропасть,
    // а итог уже уходит в toast через onTerminal.
    if (next && !isProjectOperationActive(next)) {
      if (terminalHandledRef.current !== next.id) {
        terminalHandledRef.current = next.id;
        onTerminalRef.current?.(next);
      }
      operationRef.current = null;
      setOperation(null);
      return;
    }
    operationRef.current = next;
    setOperation(next);
  }, []);

  const refresh = useCallback(async (): Promise<ProjectOperation | null> => {
    const current = operationRef.current;
    if (!current) return null;
    const generation = scopeGenerationRef.current;
    try {
      const next = await fetchProjectOperation(current.id);
      if (generation !== scopeGenerationRef.current || operationRef.current?.id !== current.id) {
        return operationRef.current;
      }
      setError(null);
      pollingErrorNotifiedRef.current = false;
      updateOperation(next);
      return next;
    } catch (err: unknown) {
      if (generation !== scopeGenerationRef.current || operationRef.current?.id !== current.id) {
        return operationRef.current;
      }
      const nextError = err instanceof Error ? err : new Error('Не удалось получить состояние операции.');
      setError(nextError);
      if (!pollingErrorNotifiedRef.current) {
        pollingErrorNotifiedRef.current = true;
        window.dispatchEvent(new CustomEvent('app-toast', {
          detail: 'Не удалось обновить статус операции. Повторим автоматически.',
        }));
      }
      return current;
    }
  }, [updateOperation]);

  useEffect(() => {
    let cancelled = false;
    const generation = scopeGenerationRef.current + 1;
    scopeGenerationRef.current = generation;
    pollingErrorNotifiedRef.current = false;
    operationRef.current = null;
    terminalHandledRef.current = null;
    setOperation(null);
    setError(null);
    if (!enabled) {
      setLoading(false);
      return () => { cancelled = true; };
    }
    setLoading(true);
    fetchActiveProjectOperation(clientId)
      .then((active) => {
        if (!cancelled && generation === scopeGenerationRef.current && active) updateOperation(active);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error('Не удалось получить состояние операции.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [clientId, enabled, updateOperation]);

  useEffect(() => {
    if (!enabled || !operation || !isProjectOperationActive(operation)) return;
    let cancelled = false;
    let timer: number | undefined;
    const schedule = () => {
      timer = window.setTimeout(async () => {
        if (cancelled) return;
        await refresh();
        if (!cancelled && operationRef.current && isProjectOperationActive(operationRef.current)) schedule();
      }, pollIntervalMs);
    };
    schedule();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [enabled, operation, pollIntervalMs, refresh]);

  const start = useCallback((next: ProjectOperation) => {
    terminalHandledRef.current = null;
    pollingErrorNotifiedRef.current = false;
    setError(null);
    updateOperation(next);
  }, [updateOperation]);

  const clear = useCallback(() => {
    terminalHandledRef.current = null;
    updateOperation(null);
  }, [updateOperation]);

  return { operation, loading, error, start, refresh, clear };
}
