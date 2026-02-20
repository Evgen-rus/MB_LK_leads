// Экран "Изменения клиента" для админа.
// Показывает список необработанных изменений по проектам выбранного клиента.
import { useEffect, useMemo, useState } from 'react';
import { fetchAdminClientChanges, resolveAdminChange, type AdminChange, type AdminChangeStatus } from '../api';
import ChangeProjectDiffModal from './ChangeProjectDiffModal';
import DateTimeCompact from './DateTimeCompact';

type ResolvedPayload = {
  change: AdminChange;
  processed?: number;
  batchId?: string | null;
};

type AdminClientChangesProps = {
  clientId: number;
  clientName: string;
  // onResolvedChange вызывается после успешной отметки изменения как выполненного.
  // Передаём изменение + количество обработанных (для батчей) + batchId.
  onResolvedChange?: (payload: ResolvedPayload) => void;
};

type GroupedChanges = {
  projectId: number | null;
  projectName: string;
  items: AdminChange[];
};

// Внутренний тип с временными полями для агрегации батчей созданий
type AdminChangeWithMeta = AdminChange & { _batchCount?: number; _sources?: Set<string> };

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function shortNameSummary(changes: AdminChange[]): string | null {
  const normalize = (name: string) => name.replace(/^B[1-4][\s_-]*/i, '');
  // Используем имя из snapshot для созданий, чтобы не выводить "Несколько проектов"
  const names = changes
    .map((c) => {
      const snapName =
        c.projectSnapshot &&
        typeof c.projectSnapshot === 'object' &&
        'name' in c.projectSnapshot &&
        typeof (c.projectSnapshot as { name?: unknown }).name === 'string'
          ? (c.projectSnapshot as { name?: string }).name
          : undefined;
      const raw = snapName || c.projectName || '';
      return raw ? normalize(raw) : '';
    })
    .filter(Boolean);

  if (!names.length) return null;

  const unique = Array.from(new Set(names));
  if (unique.length === 1) {
    return `${unique[0]}`;
  }
  return unique.length <= 2 ? unique.join(', ') : `${unique.slice(0, 2).join(', ')}, …`;
}

function groupByProject(changes: AdminChange[]): GroupedChanges[] {
  const map = new Map<string, GroupedChanges>();
  changes.forEach((c) => {
    const pid = c.projectId ?? 0;
    const name = c.projectName || 'Без проекта';
    const key = `${pid}::${name}`;
    if (!map.has(key)) {
      map.set(key, { projectId: c.projectId ?? null, projectName: name, items: [] });
    }
    map.get(key)!.items.push(c);
  });
  return Array.from(map.values());
}

function AdminClientChanges({ clientId, clientName, onResolvedChange }: AdminClientChangesProps) {
  const [allItems, setAllItems] = useState<AdminChange[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [snapshotFor, setSnapshotFor] = useState<AdminChange | null>(null);
  const [filterMode, setFilterMode] = useState<'changes' | 'creates' | 'all'>('changes');
  const counts = useMemo(() => {
    let creates = 0;
    let updates = 0;
    allItems.forEach((c) => {
      const status = (c.status as AdminChangeStatus | undefined) ?? 'pending';
      if (status === 'done') return;
      if (c.action === 'create') creates += 1;
      else updates += 1;
    });
    return {
      creates,
      updates,
      total: creates + updates,
    };
  }, [allItems]);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const resp = await fetchAdminClientChanges(clientId, { actions: ['create', 'update', 'delete'] });
        setAllItems(resp.items);
      } catch (err: unknown) {
        console.error(err);
        setError(getErrorMessage(err, 'Не удалось загрузить изменения клиента'));
      } finally {
        setLoading(false);
      }
    })();
  }, [clientId]);

  const items = useMemo(() => {
    if (filterMode === 'creates') return allItems.filter((c) => c.action === 'create');
    if (filterMode === 'changes') return allItems.filter((c) => c.action === 'update' || c.action === 'delete');
    return allItems;
  }, [allItems, filterMode]);

  const grouped = useMemo(() => {
    // Агрегируем создания с одним batchId в одну карточку
    const createsByBatch = new Map<string, AdminChangeWithMeta>();
    const rest: AdminChange[] = [];
    items.forEach((c) => {
      if (c.action === 'create' && c.batchId) {
        const existing = createsByBatch.get(c.batchId);
        if (!existing) {
          // кладём исходное событие + временные поля для накопления
          const sources = new Set<string>();
          const sourceLimits: Record<string, number> = {};
          const tryCollectSource = (snap?: Record<string, unknown> | null) => {
            if (!snap || typeof snap !== 'object') return;
            const code = 'dataSourceCode' in snap && typeof (snap as { dataSourceCode?: unknown }).dataSourceCode === 'string'
              ? (snap as { dataSourceCode: string }).dataSourceCode
              : null;
            if (code) sources.add(code);

            const limitRaw = 'dataLimit' in snap ? (snap as { dataLimit?: unknown }).dataLimit : null;
            const limit = typeof limitRaw === 'number' && Number.isFinite(limitRaw) ? limitRaw : null;
            if (code && limit != null) {
              sourceLimits[code] = limit;
            }
          };
          tryCollectSource(c.projectSnapshot);
          const sortedSources = Array.from(sources).sort();
          const limitsStr = sortedSources
            .filter((s) => typeof sourceLimits[s] === 'number')
            .map((s) => `${s}: ${sourceLimits[s]}`)
            .join(', ');
          const seed: AdminChangeWithMeta = {
            ...c,
            _batchCount: 1,
            _sources: sources,
            sources: sortedSources,
            sourceLimits,
            // Для батча чуть уточняем описание: какие источники и какие лимиты по ним
            description: c.description + (limitsStr ? ` | Лимиты: ${limitsStr}` : (sortedSources.length ? ` | Источники: ${sortedSources.join(', ')}` : '')),
          };
          createsByBatch.set(c.batchId, seed);
        } else {
          // Обновляем описание, projectName и полный набор источников
          const projects = new Set<string>();
          if (existing.projectName) projects.add(existing.projectName);
          if (c.projectName) projects.add(c.projectName);
          const sources = existing._sources ? new Set<string>(existing._sources) : new Set<string>();
          const sourceLimits: Record<string, number> = (existing.sourceLimits && typeof existing.sourceLimits === 'object')
            ? { ...existing.sourceLimits }
            : {};
          const tryCollectSource = (snap?: Record<string, unknown> | null) => {
            if (!snap || typeof snap !== 'object') return;
            const code = 'dataSourceCode' in snap && typeof (snap as { dataSourceCode?: unknown }).dataSourceCode === 'string'
              ? (snap as { dataSourceCode: string }).dataSourceCode
              : null;
            if (code) sources.add(code);

            const limitRaw = 'dataLimit' in snap ? (snap as { dataLimit?: unknown }).dataLimit : null;
            const limit = typeof limitRaw === 'number' && Number.isFinite(limitRaw) ? limitRaw : null;
            if (code && limit != null) {
              sourceLimits[code] = limit;
            }
          };
          tryCollectSource(existing.projectSnapshot);
          tryCollectSource(c.projectSnapshot);
          const count = existing._batchCount ? existing._batchCount + 1 : 2;
          const sortedSources = Array.from(sources).sort();
          const limitsStr = sortedSources
            .filter((s) => typeof sourceLimits[s] === 'number')
            .map((s) => `${s}: ${sourceLimits[s]}`)
            .join(', ');
          const updated: AdminChangeWithMeta = {
            ...existing,
            projectName: projects.size > 1 ? 'с несколькими источниками' : Array.from(projects)[0] || existing.projectName,
            description: `Создано проектов: ${count}${sortedSources.length ? ` | Источники: ${sortedSources.join(', ')}` : ''}${limitsStr ? ` | Лимиты: ${limitsStr}` : ''}`,
            projectSnapshot: existing.projectSnapshot,
            sources: sortedSources,
            sourceLimits,
          };
          updated._batchCount = count;
          updated._sources = sources;
          createsByBatch.set(c.batchId, updated);
        }
      } else {
        rest.push(c);
      }
    });
    const merged = [...rest, ...Array.from(createsByBatch.values())];
    return groupByProject(merged);
  }, [items]);

  async function handleResolve(change: AdminChange) {
    const id = change.id;
    try {
      const resp = await resolveAdminChange(id);
      const processedCount = typeof resp?.processed === 'number' ? resp.processed : 1;
      if (resp?.batch) {
        // Убираем все изменения этого батча
        setAllItems((prev) => prev.filter((c) => c.batchId !== resp.batch));
      } else {
        setAllItems((prev) => prev.filter((c) => c.id !== id));
      }
      onResolvedChange?.({
        change,
        processed: processedCount,
        batchId: resp?.batch ?? null,
      });
    } catch (err: unknown) {
      console.error(err);
      setError(getErrorMessage(err, 'Не удалось отметить изменение как обработанное'));
    }
  }

  return (
    <div className="table-card">
      <div
        style={{
          padding: 16,
          borderBottom: '1px solid #eee',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div>
          <div style={{ fontWeight: 600 }}>Изменения клиента</div>
          <div className="sub">
            {clientName} (id: {clientId})
          </div>
        </div>
        <div className="sub">
          Необработанных всего: {counts.total}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            className={filterMode === 'changes' ? 'btn btn--primary' : 'btn btn--secondary'}
            onClick={() => setFilterMode('changes')}
          >
            Изменения
            {counts.updates > 0 && (
              <span className="badge badge--orange" style={{ marginLeft: 8, fontWeight: 500 }}>
                {counts.updates}
              </span>
            )}
          </button>
          <button
            type="button"
            className={filterMode === 'creates' ? 'btn btn--primary' : 'btn btn--secondary'}
            onClick={() => setFilterMode('creates')}
          >
            Создания
            {counts.creates > 0 && (
              <span className="badge badge--gray" style={{ marginLeft: 8, fontWeight: 500 }}>
                {counts.creates}
              </span>
            )}
          </button>
          <button
            type="button"
            className={filterMode === 'all' ? 'btn btn--primary' : 'btn btn--secondary'}
            onClick={() => setFilterMode('all')}
          >
            Все
          </button>
        </div>
      </div>

      <div style={{ padding: 16 }}>
        {error && (
          <div style={{ color: '#d00', marginBottom: 12 }}>
            {error}
          </div>
        )}
        {loading && !error && (
          <div className="muted" style={{ padding: 8 }}>
            Загрузка изменений…
          </div>
        )}
        {!loading && !error && items.length === 0 && (
          <div className="muted" style={{ padding: 8 }}>
            Необработанных изменений нет.
          </div>
        )}

        {!loading && !error && grouped.map((g) => (
          <div
            key={`${g.projectId ?? 0}::${g.projectName}`}
            style={{
              border: '1px solid #eee',
              borderRadius: 8,
              padding: 12,
              marginBottom: 12,
            }}
          >
            <div style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div className="sub">Проект</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span>{g.projectName}</span>
                  {g.projectId != null && (
                    <span className="sub">
                      (id: {g.projectId})
                    </span>
                  )}
                  {/* Сводка по именам в батче, чтобы админ видел название(я) сразу */}
                  {g.items[0]?.action === 'create' && g.items.length > 0 && (() => {
                    const summary = shortNameSummary(g.items);
                    return summary ? (
                      <span className="sub" style={{ color: '#555' }}>
                        Название: {summary}
                      </span>
                    ) : null;
                  })()}
                </div>
              </div>
              <div className="sub">
                Изменений: {g.items.length}
              </div>
            </div>
            <div className="table-scroll">
              <table className="table" style={{ margin: 0 }}>
                <thead>
                  <tr>
                    <th style={{ width: '24%' }}>Когда</th>
                    <th style={{ width: '14%' }}>Действие</th>
                    <th style={{ width: '18%' }}>Кто</th>
                    <th>Описание изменения</th>
                    <th style={{ width: '16%' }}>Статус</th>
                    <th style={{ width: 210 }}>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {g.items.map((c) => (
                    <tr key={c.id}>
                      <td className="muted" style={{ whiteSpace: 'nowrap' }}><DateTimeCompact value={c.createdAt} /></td>
                      <td className="muted">
                        {c.action === 'create' ? 'Создание' : c.action === 'delete' ? 'Удаление' : 'Изменение'}
                      </td>
                      <td>
                        {c.actor ? (
                          <div>
                            <div className="name">{c.actor.login}</div>
                            <div className="sub" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                              <span>id: {c.actor.id}</span>
                              {c.actorMode === 'admin_impersonation' && (
                                <span className="badge badge--gray">через имперсонацию</span>
                              )}
                            </div>
                          </div>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>{c.description}</td>
                      <td>
                        {(() => {
                          const status = (c.status as AdminChangeStatus | undefined) ?? 'pending';
                          const label =
                            status === 'pending'
                              ? 'Не выполнено'
                              : 'Выполнено';
                          const cls =
                            status === 'pending'
                              ? 'badge badge--gray'
                              : 'badge badge--green';
                          return <span className={cls}>{label}</span>;
                        })()}
                      </td>
                      <td>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                          <button
                            type="button"
                            className="btn btn--secondary"
                            style={{ width: '100%' }}
                            onClick={() => handleResolve(c)}
                          >
                            Отметить выполненным
                          </button>
                          <button
                            type="button"
                            className="btn"
                            style={{ width: '100%' }}
                            onClick={() => setSnapshotFor(c)}
                            disabled={!c.projectSnapshot}
                            title={c.projectSnapshot ? 'Открыть карточку' : 'Нет данных карточки'}
                          >
                            Карточка
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </div>

      {snapshotFor && (
        <ChangeProjectDiffModal
          change={snapshotFor}
          onClose={() => setSnapshotFor(null)}
        />
      )}
    </div>
  );
}

export default AdminClientChanges;


