import { useMemo, useState } from 'react';
import type { Project } from '../types/project';
import { normalizePhonesMultiline } from '../utils/phones';
import BulkEditModalFrame from './BulkEditModalFrame';

type ContactsTarget = 'calls' | 'sites';

type BulkEditContactsModalSubmit = {
  target: ContactsTarget;
  values: string[];
};

type BulkEditContactsModalProps = {
  selectedProjects: Project[];
  submitting?: boolean;
  onClose: () => void;
  onSubmit: (payload: BulkEditContactsModalSubmit) => void;
};

const CALLS_SOURCES = new Set(['Звонки', 'Ретрозвонки', 'Пересечение']);
const SITES_SOURCES = new Set(['Сайты', 'Ретросайты', 'Пересечение']);

function parseSites(text: string): string[] {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  return Array.from(new Set(lines));
}

function BulkEditContactsModal({
  selectedProjects,
  submitting = false,
  onClose,
  onSubmit,
}: BulkEditContactsModalProps) {
  const callsCount = useMemo(
    () =>
      selectedProjects.filter((project) => CALLS_SOURCES.has(project.collectionSource)).length,
    [selectedProjects],
  );
  const sitesCount = useMemo(
    () =>
      selectedProjects.filter((project) => SITES_SOURCES.has(project.collectionSource)).length,
    [selectedProjects],
  );

  const defaultTarget: ContactsTarget = callsCount > 0 ? 'calls' : 'sites';
  const [target, setTarget] = useState<ContactsTarget>(defaultTarget);
  const [textValue, setTextValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  const hasBothTargets = callsCount > 0 && sitesCount > 0;

  function handleSubmit() {
    if (target === 'calls') {
      const normalized = normalizePhonesMultiline(textValue);
      setTextValue(normalized.displayText);
      if (normalized.errors.length > 0) {
        const details = normalized.errors
          .slice(0, 4)
          .map((item) => `строка ${item.lineNumber}: "${item.raw}" (${item.reason})`)
          .join('\n');
        setError(`Некорректные номера:\n${details}`);
        return;
      }
      if (normalized.normalized.length === 0) {
        setError('Укажите хотя бы один номер телефона.');
        return;
      }
      setError(null);
      onSubmit({ target, values: normalized.normalized });
      return;
    }

    const sites = parseSites(textValue);
    if (sites.length === 0) {
      setError('Укажите хотя бы один сайт.');
      return;
    }
    setError(null);
    onSubmit({ target, values: sites });
  }

  return (
    <BulkEditModalFrame
      selectedCount={selectedProjects.length}
      onClose={onClose}
      onSubmit={handleSubmit}
      submitting={submitting}
    >
      <div className="section-title">Телефоны/сайты конкурентов</div>

      {hasBothTargets && (
        <div style={{ display: 'grid', gap: 8 }}>
          <label>
            <input
              type="radio"
              name="bulk-contacts-target"
              checked={target === 'calls'}
              onChange={() => setTarget('calls')}
              disabled={submitting}
            />{' '}
            Телефоны - {callsCount} проекта(ов)
          </label>
          <label>
            <input
              type="radio"
              name="bulk-contacts-target"
              checked={target === 'sites'}
              onChange={() => setTarget('sites')}
              disabled={submitting}
            />{' '}
            Сайты - {sitesCount} проекта(ов)
          </label>
        </div>
      )}

      <label style={{ display: 'grid', gap: 6 }}>
        <span className="section-title">
          {target === 'calls'
            ? 'Телефоны конкурентов/целевых компаний'
            : 'Сайты конкурентов/целевых компаний'}
        </span>
        <span className="hint">
          {target === 'calls'
            ? 'По одному номеру в строке, строго 11 цифр и первая 7.'
            : 'По одному сайту в строке.'}
        </span>
        <textarea
          rows={8}
          value={textValue}
          onChange={(e) => setTextValue(e.target.value)}
          disabled={submitting}
          placeholder={target === 'calls' ? '79231234567' : 'site.ru'}
          style={{
            fontFamily:
              'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
          }}
        />
      </label>

      {error && (
        <div className="sub" style={{ color: '#b42318', whiteSpace: 'pre-line' }}>
          {error}
        </div>
      )}
    </BulkEditModalFrame>
  );
}

export type { BulkEditContactsModalSubmit, ContactsTarget };
export default BulkEditContactsModal;
