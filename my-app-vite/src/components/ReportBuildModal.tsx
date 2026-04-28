import { useMemo, useState } from 'react';
import { formatProjectNameForDisplay } from '../utils/sourceCodeDisplay';

type ReportFormat = 'csv' | 'xlsx';

type UserOption = {
  id: number;
  name: string;
};

type ProjectOption = {
  id: number;
  name: string;
};

type ClientSelection = number | 'all' | null;

type ReportBuildModalSubmitPayload = {
  fromDate: string;
  toDate: string;
  format: ReportFormat;
  projectIds?: number[];
  clientId?: number | null;
};

type ReportBuildModalProps = {
  title: string;
  onClose: () => void;
  onSubmit: (payload: ReportBuildModalSubmitPayload) => Promise<void>;
  submitting: boolean;
  users?: UserOption[];
  selectedClientId?: ClientSelection;
  onClientChange?: (clientId: ClientSelection) => void;
  allowAllClients?: boolean;
  projects: ProjectOption[];
  projectsLoading?: boolean;
};

function formatDateInput(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function ReportBuildModal({
  title,
  onClose,
  onSubmit,
  submitting,
  users,
  selectedClientId = null,
  onClientChange,
  allowAllClients = false,
  projects,
  projectsLoading = false,
}: ReportBuildModalProps) {
  const today = useMemo(() => formatDateInput(new Date()), []);
  const [fromDate, setFromDate] = useState<string>(today);
  const [toDate, setToDate] = useState<string>(today);
  const [format, setFormat] = useState<ReportFormat>('xlsx');
  const [projectMode, setProjectMode] = useState<'all' | 'selected'>('all');
  const [selectedProjectIds, setSelectedProjectIds] = useState<number[]>([]);
  const [query, setQuery] = useState('');

  const filteredProjects = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return projects;
    return projects.filter((p) => formatProjectNameForDisplay(p.name).toLowerCase().includes(q));
  }, [projects, query]);

  const hasClientSelector = Array.isArray(users);
  const allClientsSelected = hasClientSelector && selectedClientId === 'all';
  const selectedNumericClientId = typeof selectedClientId === 'number' ? selectedClientId : undefined;
  const selectedClientValue = selectedClientId === 'all' ? 'all' : selectedClientId ?? '';
  const dateInvalid = !fromDate || !toDate || fromDate > toDate;
  const projectsInvalid = !allClientsSelected && projectMode === 'selected' && selectedProjectIds.length === 0;
  const clientInvalid = hasClientSelector && !allClientsSelected && selectedNumericClientId == null;
  const submitDisabled = submitting || dateInvalid || projectsInvalid || clientInvalid;

  function toggleProject(projectId: number) {
    setSelectedProjectIds((prev) =>
      prev.includes(projectId) ? prev.filter((id) => id !== projectId) : [...prev, projectId],
    );
  }

  function handleClientSelect(value: string) {
    if (value === 'all') {
      setProjectMode('all');
      setSelectedProjectIds([]);
      onClientChange?.('all');
      return;
    }
    onClientChange?.(value ? Number(value) : null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitDisabled) return;
    await onSubmit({
      fromDate,
      toDate,
      format,
      projectIds: allClientsSelected || projectMode === 'all' ? undefined : selectedProjectIds,
      clientId: hasClientSelector ? (allClientsSelected ? null : selectedNumericClientId) : undefined,
    });
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1400,
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="modal-card"
        style={{
          background: '#fff',
          borderRadius: 10,
          width: '100%',
          maxWidth: 640,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>{title}</div>
          <button type="button" className="btn btn--ghost" onClick={onClose} style={{ padding: '6px 10px' }}>
            ✕
          </button>
        </div>
        <form onSubmit={handleSubmit} style={{ padding: 20, display: 'grid', gap: 14 }}>
          {hasClientSelector && (
            <label style={{ display: 'grid', gap: 6 }}>
              <span className="section-title">Клиент</span>
              <select
                value={selectedClientValue}
                onChange={(e) => handleClientSelect(e.target.value)}
              >
                <option value="">Выберите клиента</option>
                {allowAllClients && <option value="all">Все клиенты</option>}
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Проекты</span>
            <div className="radio-row">
              <label>
                <input
                  type="radio"
                  name="projectMode"
                  checked={allClientsSelected || projectMode === 'all'}
                  onChange={() => setProjectMode('all')}
                />{' '}
                Все проекты
              </label>
              <label>
                <input
                  type="radio"
                  name="projectMode"
                  checked={projectMode === 'selected'}
                  onChange={() => setProjectMode('selected')}
                  disabled={allClientsSelected}
                />{' '}
                Выбрать проекты
              </label>
            </div>
            {allClientsSelected && (
              <div className="sub">Для отчёта по всем клиентам используется режим “Все проекты”.</div>
            )}
            {!allClientsSelected && projectMode === 'selected' && (
              <div style={{ display: 'grid', gap: 8 }}>
                <input
                  type="search"
                  placeholder="Поиск по названию"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <div style={{ maxHeight: 220, overflowY: 'auto', border: '1px solid #e6e6ee', borderRadius: 10, padding: 8 }}>
                  {projectsLoading && <div className="sub">Загрузка проектов…</div>}
                  {!projectsLoading && filteredProjects.length === 0 && (
                    <div className="sub">По запросу ничего не найдено.</div>
                  )}
                  {!projectsLoading &&
                    filteredProjects.map((project) => (
                      <label key={project.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 4px' }}>
                        <input
                          type="checkbox"
                          checked={selectedProjectIds.includes(project.id)}
                          onChange={() => toggleProject(project.id)}
                        />
                        <span>{formatProjectNameForDisplay(project.name)}</span>
                      </label>
                    ))}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button type="button" className="btn" onClick={() => setSelectedProjectIds([])}>
                    Очистить выбор
                  </button>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setSelectedProjectIds(projects.map((p) => p.id))}
                    disabled={projectsLoading || projects.length === 0}
                  >
                    Все проекты
                  </button>
                </div>
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Период</span>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <label style={{ display: 'grid', gap: 4 }}>
                <span className="sub">С</span>
                <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
              </label>
              <label style={{ display: 'grid', gap: 4 }}>
                <span className="sub">По</span>
                <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
              </label>
            </div>
            {dateInvalid && <div className="sub" style={{ color: '#d00' }}>Проверьте период: дата "С" не должна быть позже даты "По".</div>}
          </div>

          <label style={{ display: 'grid', gap: 6 }}>
            <span className="section-title">Формат</span>
            <select value={format} onChange={(e) => setFormat(e.target.value as ReportFormat)}>
              <option value="xlsx">XLSX</option>
              <option value="csv">CSV</option>
            </select>
          </label>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, borderTop: '1px solid #eee', paddingTop: 12 }}>
            <button type="button" className="btn" onClick={onClose} disabled={submitting}>
              Отмена
            </button>
            <button type="submit" className="btn btn--primary" disabled={submitDisabled}>
              {submitting ? 'Формируем…' : 'Сформировать отчёт'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default ReportBuildModal;
