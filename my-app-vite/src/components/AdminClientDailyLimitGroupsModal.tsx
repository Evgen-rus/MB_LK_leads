// Модалка «Лимиты групп проектов на день» для админа.
//
// Модалка НЕ часть AdminClientCardModal: ею управляют из «Сводки клиента».
// Источник истины членства — конкретные project_id; LR-код используется
// только как удобный поисковый запрос, который помогает быстро найти и
// массово отметить проекты.
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  createDailyExportLimitGroup,
  deleteDailyExportLimitGroup,
  fetchDailyExportLimitGroups,
  searchDailyExportLimitGroupProjects,
  updateDailyExportLimitGroup,
  type DailyExportLimitGroup,
  type DailyExportLimitGroupProject,
} from '../api';
import { preventNumberInputWheel } from '../utils/numberInput';
import { formatProjectNameForDisplay } from '../utils/sourceCodeDisplay';

type NumericInputValue = number | '';

type AdminClientDailyLimitGroupsModalProps = {
  clientId: number;
  clientName: string;
  onClose: () => void;
  onChanged?: () => void;
};

type EditorState =
  | { mode: 'create' }
  | { mode: 'edit'; group: DailyExportLimitGroup }
  | null;

// Потолок одной поисковой выдачи.  Совпадает с серверным LIMIT_GROUP_PROJECT_SEARCH_MAX:
// «Выбрать все найденные» должно реально выбирать все найденные, а не первые 200.
const PROJECT_SEARCH_LIMIT = 1000;

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function parseNumberInputValue(raw: string): NumericInputValue {
  if (!raw.trim()) return '';
  const next = Number(raw);
  return Number.isFinite(next) ? next : '';
}

/** Визуальный стиль существующих модалок проекта. */
function ModalShell({
  title,
  subtitle,
  onClose,
  children,
  width = 720,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: React.ReactNode;
  width?: number;
}) {
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="modal modal-card"
        style={{
          width: '100%',
          maxWidth: width,
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 12,
          background: '#fff',
        }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div
          style={{
            padding: '20px 24px',
            borderBottom: '1px solid #e9edf6',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            gap: 12,
          }}
        >
          <div style={{ display: 'grid', gap: 4 }}>
            <div style={{ fontSize: '1.125rem', fontWeight: 700 }}>{title}</div>
            {subtitle && <div className="sub">{subtitle}</div>}
          </div>
          <button type="button" className="btn btn--ghost" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>
        <div style={{ padding: 20, overflow: 'auto', display: 'grid', gap: 14 }}>{children}</div>
      </div>
    </div>
  );
}

function GroupEditor({
  clientId,
  editor,
  onCancel,
  onSaved,
}: {
  clientId: number;
  editor: Exclude<EditorState, null>;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const isCreate = editor.mode === 'create';

  const [name, setName] = useState(isCreate ? '' : editor.group.name);
  const [dailyLimit, setDailyLimit] = useState<NumericInputValue>(
    isCreate ? '' : editor.group.dailyLimit,
  );
  const [selectedIds, setSelectedIds] = useState<number[]>(isCreate ? [] : editor.group.projectIds);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DailyExportLimitGroupProject[]>([]);
  const [totalFound, setTotalFound] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Выбранные проекты храним по id, но показываем их карточками независимо
  // от текущего поискового запроса — иначе смена запроса «теряла» бы выбор.
  const [selectedProjects, setSelectedProjects] = useState<DailyExportLimitGroupProject[]>([]);

  const loadProjects = useCallback(
    async (search: string) => {
      setSearching(true);
      try {
        const resp = await searchDailyExportLimitGroupProjects(clientId, {
          q: search.trim() || undefined,
          limit: PROJECT_SEARCH_LIMIT,
        });
        setResults(resp.items);
        setTotalFound(resp.total);
        setTruncated(resp.truncated);
      } catch (err: unknown) {
        setError(getErrorMessage(err, 'Не удалось загрузить проекты'));
      } finally {
        setSearching(false);
      }
    },
    [clientId],
  );

  // При открытии редактора подтягиваем названия проектов, уже входящих
  // в группу: без этого пользователь не видел бы свой текущий состав.
  useEffect(() => {
    if (isCreate) {
      setSelectedProjects([]);
      return;
    }
    const existingIds = editor.group.projectIds;
    if (existingIds.length === 0) {
      setSelectedProjects([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const resp = await searchDailyExportLimitGroupProjects(clientId, {
          projectIds: existingIds,
        });
        if (!cancelled) setSelectedProjects(resp.items);
      } catch {
        if (!cancelled) setSelectedProjects([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [clientId, editor, isCreate]);

  useEffect(() => {
    void loadProjects('');
  }, [loadProjects]);

  // Поиск с небольшой задержкой, чтобы не дёргать API на каждый символ.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      void loadProjects(query);
    }, 250);
    return () => window.clearTimeout(handle);
  }, [loadProjects, query]);

  const selectedIdSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  // Уже выбранные, которых нет в текущей выдаче поиска.  Их нельзя просто
  // не рисовать: пользователь должен видеть и суметь снять свой выбор.
  const resultIdSet = useMemo(() => new Set(results.map((item) => item.id)), [results]);
  const selectedHidden = useMemo(
    () => selectedProjects.filter((project) => !resultIdSet.has(project.id)),
    [selectedProjects, resultIdSet],
  );

  function isLockedByOtherGroup(project: DailyExportLimitGroupProject): boolean {
    if (project.limitGroupId == null) return false;
    if (isCreate) return true;
    return project.limitGroupId !== editor.group.id;
  }

  function toggleProject(project: DailyExportLimitGroupProject) {
    if (isLockedByOtherGroup(project)) return;
    setSelectedIds((prev) => {
      if (prev.includes(project.id)) {
        setSelectedProjects((items) => items.filter((item) => item.id !== project.id));
        return prev.filter((value) => value !== project.id);
      }
      setSelectedProjects((items) => [...items, project]);
      return [...prev, project.id];
    });
  }

  function selectAllFound() {
    const selectable = results.filter(
      (project) => !isLockedByOtherGroup(project) && !selectedIdSet.has(project.id),
    );
    if (selectable.length === 0) return;
    setSelectedIds((prev) => [...prev, ...selectable.map((project) => project.id)]);
    setSelectedProjects((items) => {
      const known = new Set(items.map((item) => item.id));
      return [...items, ...selectable.filter((project) => !known.has(project.id))];
    });
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const limitNumber = dailyLimit === '' ? 0 : Number(dailyLimit);
    if (!name.trim()) {
      setError('Укажите название группы');
      return;
    }
    if (limitNumber <= 0) {
      setError('Укажите дневной лимит больше нуля');
      return;
    }
    if (selectedIds.length === 0) {
      setError('Выберите хотя бы один проект');
      return;
    }

    try {
      setSubmitting(true);
      setError(null);
      const payload = { name: name.trim(), dailyLimit: limitNumber, projectIds: selectedIds };
      if (isCreate) {
        await createDailyExportLimitGroup(clientId, payload);
      } else {
        await updateDailyExportLimitGroup(editor.group.id, payload);
      }
      onSaved();
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось сохранить группу'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'grid', gap: 16 }}>
      <label style={{ display: 'grid', gap: 6 }}>
        <span className="section-title">Название группы</span>
        <input
          autoFocus
          type="text"
          value={name}
          placeholder="Например: [LR223] ПрактикМ"
          onChange={(e) => setName(e.target.value)}
          required
        />
      </label>

      <label style={{ display: 'grid', gap: 6, maxWidth: 260 }}>
        <span className="section-title">Лимит на день</span>
        <input
          type="number"
          min={1}
          value={dailyLimit}
          onChange={(e) => setDailyLimit(parseNumberInputValue(e.target.value))}
          onWheel={preventNumberInputWheel}
          required
        />
      </label>

      <div style={{ display: 'grid', gap: 8 }}>
        <div className="section-title" style={{ margin: 0 }}>Проекты группы</div>
        <div className="sub">
          Поиск работает по части названия и по LR-коду: например <code>LR223</code> или{' '}
          <code>[LR223]</code>. Найденные проекты можно отметить сразу все.
        </div>

        <input
          type="search"
          value={query}
          placeholder="Поиск: LR223, [LR223] или часть названия"
          onChange={(e) => setQuery(e.target.value)}
        />

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="btn btn--secondary"
            onClick={selectAllFound}
            disabled={searching || results.length === 0}
          >
            Выбрать все найденные
          </button>
          <span className="sub">
            {searching
              ? 'Загрузка…'
              : truncated
                // Честно говорим, что выдача обрезана: иначе «все найденные»
                // обещало бы 300 проектов, а выбрало бы только первые.
                ? `Показаны первые ${results.length} из ${totalFound} · Выбрано: ${selectedIds.length} · уточните поиск`
                : `Найдено: ${totalFound} · Выбрано: ${selectedIds.length}`}
          </span>
        </div>

        {/*
          Один список: найденные проекты, где уже выбранные отмечены
          чекбоксом.  Раньше здесь было два окна — «Найдено» и
          «Выбрано в группу», — и выбранные проекты показывались дважды.
          Список выбранных отдельно нужен только затем, чтобы не терять
          выбор при смене поискового запроса, поэтому невидимые (не
          попавшие в выдачу) участники выводятся сверху, отдельным блоком.
        */}
        {selectedHidden.length > 0 && (
          <div
            style={{
              display: 'grid',
              gap: 6,
              padding: 10,
              border: '1px solid #e7e9f5',
              borderRadius: 10,
              background: '#f7f8fc',
            }}
          >
            <div className="sub">
              Уже выбрано, но не попало в поиск: {selectedHidden.length}
            </div>
            {selectedHidden.map((project) => (
              <label
                key={project.id}
                style={{ display: 'flex', gap: 8, alignItems: 'flex-start', cursor: 'pointer' }}
              >
                <input
                  type="checkbox"
                  checked
                  onChange={() => toggleProject(project)}
                  style={{ width: 'auto', marginTop: 2 }}
                />
                <span style={{ fontSize: '0.85rem' }}>
                  {formatProjectNameForDisplay(project.name)}{' '}
                  <span className="sub">(id: {project.id})</span>
                </span>
              </label>
            ))}
          </div>
        )}

        <div
          style={{
            display: 'grid',
            gap: 4,
            maxHeight: 280,
            overflow: 'auto',
            border: '1px solid #ececf2',
            borderRadius: 10,
            padding: 8,
          }}
        >
          {results.length === 0 && !searching && (
            <div className="sub" style={{ padding: 6 }}>Ничего не найдено.</div>
          )}
          {results.map((project) => {
            const locked = isLockedByOtherGroup(project);
            return (
              <label
                key={project.id}
                style={{
                  display: 'flex',
                  gap: 8,
                  alignItems: 'flex-start',
                  padding: '6px 4px',
                  cursor: locked ? 'not-allowed' : 'pointer',
                  opacity: locked ? 0.6 : 1,
                }}
              >
                <input
                  type="checkbox"
                  checked={selectedIdSet.has(project.id)}
                  disabled={locked}
                  onChange={() => toggleProject(project)}
                  style={{ width: 'auto', marginTop: 2 }}
                />
                <span style={{ display: 'grid', gap: 2, minWidth: 0 }}>
                  <span style={{ fontSize: '0.85rem', overflowWrap: 'anywhere' }}>
                    {formatProjectNameForDisplay(project.name)}{' '}
                    <span className="sub">(id: {project.id})</span>
                  </span>
                  {locked && (
                    <span className="sub" style={{ color: '#a55' }}>
                      Уже в группе: {project.limitGroupName}
                    </span>
                  )}
                </span>
              </label>
            );
          })}
        </div>
      </div>

      {error && <div className="sub" style={{ color: '#a55' }}>{error}</div>}

      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
        <button type="button" className="btn btn--secondary" onClick={onCancel} disabled={submitting}>
          Отмена
        </button>
        <button type="submit" className="btn btn--primary" disabled={submitting}>
          {submitting ? 'Сохраняем…' : (isCreate ? 'Создать группу' : 'Сохранить')}
        </button>
      </div>
    </form>
  );
}

export default function AdminClientDailyLimitGroupsModal({
  clientId,
  clientName,
  onClose,
  onChanged,
}: AdminClientDailyLimitGroupsModalProps) {
  const [groups, setGroups] = useState<DailyExportLimitGroup[]>([]);
  const [unassignedCount, setUnassignedCount] = useState(0);
  const [unassignedTotal, setUnassignedTotal] = useState(0);
  const [showUnassigned, setShowUnassigned] = useState(false);
  const [unassignedProjects, setUnassignedProjects] = useState<DailyExportLimitGroupProject[]>([]);
  const [unassignedLoading, setUnassignedLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const loadGroups = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const resp = await fetchDailyExportLimitGroups(clientId);
      setGroups(resp.items);
      setUnassignedCount(resp.unassignedProjectsCount ?? 0);
      setUnassignedTotal(resp.unassignedProjectsTotal ?? 0);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось загрузить группы'));
      setGroups([]);
    } finally {
      setLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    void loadGroups();
  }, [loadGroups]);

  // Список вне групп грузится только по требованию: на каждый прогон он не нужен,
  // а клиент с сотнями проектов вернул бы здесь лишние сотни строк.
  useEffect(() => {
    if (!showUnassigned) {
      setUnassignedProjects([]);
      return;
    }
    if (unassignedCount === 0) return;
    let cancelled = false;
    (async () => {
      setUnassignedLoading(true);
      try {
        const resp = await searchDailyExportLimitGroupProjects(clientId, { limit: 200 });
        if (!cancelled) {
          setUnassignedProjects(resp.items.filter((item) => item.limitGroupId == null));
        }
      } catch {
        if (!cancelled) setUnassignedProjects([]);
      } finally {
        if (!cancelled) setUnassignedLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [showUnassigned, unassignedCount, clientId]);

  async function handleDelete(group: DailyExportLimitGroup) {
    if (!window.confirm(`Удалить группу «${group.name}»? Проекты группы станут обычными, лиды не удалятся.`)) {
      return;
    }
    try {
      setDeletingId(group.id);
      setError(null);
      await deleteDailyExportLimitGroup(group.id);
      await loadGroups();
      onChanged?.();
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Не удалось удалить группу'));
    } finally {
      setDeletingId(null);
    }
  }

  if (editor) {
    return (
      <ModalShell
        title={editor.mode === 'create' ? 'Новая группа проектов' : `Изменение группы: ${editor.group.name}`}
        subtitle={`Клиент: ${clientName}`}
        onClose={() => setEditor(null)}
      >
        <GroupEditor
          clientId={clientId}
          editor={editor}
          onCancel={() => setEditor(null)}
          onSaved={() => {
            setEditor(null);
            void loadGroups();
            onChanged?.();
          }}
        />
      </ModalShell>
    );
  }

  return (
    <ModalShell
      title="Лимиты групп проектов на день"
      subtitle={`Клиент: ${clientName}`}
      onClose={onClose}
      width={760}
    >
      <div className="sub">
        Лимит ограничивает только выгрузку строк в промежуточную таблицу Google.
        Проекты не отключаются и не меняют статус: лиды сверх лимита ждут своей очереди
        и уходят в следующие дни.
      </div>

      {error && <div className="sub" style={{ color: '#a55' }}>{error}</div>}

      {/*
        Предупреждение показывается только когда есть хотя бы одна группа
        И есть проекты вне них.  Пока групп нет, ограничение не настроено
        вовсе и напоминать не о чем.
      */}
      {!loading && groups.length > 0 && unassignedCount > 0 && (
        <div
          style={{
            display: 'grid',
            gap: 8,
            padding: 12,
            border: '1px solid #f0d9a8',
            borderRadius: 10,
            background: '#fffaf0',
          }}
        >
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '0.9rem', flex: 1, minWidth: 240 }}>
              Проектов вне лимитов групп: <b>{unassignedCount}</b>
              {unassignedTotal > 0 && <> из {unassignedTotal}</>}. Они выгружаются без дневного
              ограничения, пока их не добавят в группу.
            </span>
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => setShowUnassigned((v) => !v)}
            >
              {showUnassigned ? 'Скрыть' : 'Показать'}
            </button>
          </div>

          {showUnassigned && (
            <div
              style={{
                display: 'grid',
                gap: 4,
                maxHeight: 240,
                overflow: 'auto',
                border: '1px solid #f0e0c0',
                borderRadius: 8,
                padding: 8,
                background: '#fff',
              }}
            >
              {unassignedLoading && <div className="sub">Загрузка…</div>}
              {!unassignedLoading && unassignedProjects.length === 0 && (
                <div className="sub">Ничего не найдено.</div>
              )}
              {unassignedProjects.map((project) => (
                <div key={project.id} style={{ fontSize: '0.85rem', overflowWrap: 'anywhere' }}>
                  {formatProjectNameForDisplay(project.name)}{' '}
                  <span className="sub">(id: {project.id})</span>
                </div>
              ))}
              {!unassignedLoading && unassignedProjects.length > 0 && (
                <div className="sub" style={{ marginTop: 4 }}>
                  Добавьте их в группу через «Изменить» у нужной группы.
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {loading ? (
        <div className="sub">Загрузка групп…</div>
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          {groups.length === 0 && (
            <div className="sub" style={{ padding: '12px 0' }}>
              Групп пока нет. Добавьте первую, чтобы ограничить дневную выгрузку.
            </div>
          )}
          {groups.map((group) => (
            <div
              key={group.id}
              style={{
                display: 'grid',
                gap: 8,
                padding: 14,
                border: '1px solid #e7e9f5',
                borderRadius: 12,
                background: '#fbfbfe',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div style={{ display: 'grid', gap: 4, minWidth: 0 }}>
                  <div style={{ fontWeight: 700, overflowWrap: 'anywhere' }}>{group.name}</div>
                  <div className="sub">
                    Лимит на день: <b>{group.dailyLimit}</b> · Проектов: <b>{group.projectCount}</b>
                  </div>
                  <div className="sub">
                    Сегодня выгружено: <b>{group.exportedToday} / {group.dailyLimit}</b>
                    {group.pendingTotal > 0 && ` · ожидают выгрузки: ${group.pendingTotal}`}
                  </div>
                </div>
                <div style={{ display: 'grid', gap: 6, justifyItems: 'end', alignContent: 'start' }}>
                  {group.limitReached ? (
                    <span className="badge badge--orange">Лимит достигнут</span>
                  ) : (
                    <span className="badge badge--green">Активен</span>
                  )}
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button
                      type="button"
                      className="btn btn--secondary"
                      onClick={() => setEditor({ mode: 'edit', group })}
                    >
                      Изменить
                    </button>
                    <button
                      type="button"
                      className="btn btn--secondary"
                      disabled={deletingId === group.id}
                      onClick={() => void handleDelete(group)}
                    >
                      {deletingId === group.id ? 'Удаляем…' : 'Удалить'}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ))}

          <div>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => setEditor({ mode: 'create' })}
            >
              Добавить группу
            </button>
          </div>
        </div>
      )}
    </ModalShell>
  );
}
