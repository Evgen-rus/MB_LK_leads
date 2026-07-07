import { useMemo, useState } from 'react';
import {
  commitAdminPixelLeadsImport,
  commitAdminProviderLeadsImport,
  previewAdminPixelLeadsImport,
  previewAdminProviderLeadsImport,
  type AdminProviderLeadsImportCommitResp,
  type AdminProviderLeadsImportPreviewResp,
  type AdminProviderLeadsImportPreviewSample,
} from '../api';
import { formatProjectNameForDisplay } from '../utils/sourceCodeDisplay';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

const SAMPLE_LABELS: Record<string, string> = {
  rowsWithErrors: 'Ошибки в строках',
  duplicatesInFile: 'Дубли внутри файла',
  duplicatesInDb: 'Дубли уже в БД',
  notFoundProjects: 'Проекты/домены не найдены',
  ambiguousProjects: 'Неоднозначные проекты/домены',
};

type ImportKind = 'provider' | 'pixel';

type ImportCardConfig = {
  kind: ImportKind;
  title: string;
  description: string;
  requirements: string;
  previewFile: (file: File) => Promise<AdminProviderLeadsImportPreviewResp>;
  commitPreview: (previewId: string) => Promise<AdminProviderLeadsImportCommitResp>;
};

type SummaryItemProps = {
  label: string;
  value: number;
  tone?: 'default' | 'warn' | 'ok';
};

function SummaryItem({ label, value, tone = 'default' }: SummaryItemProps) {
  const color =
    tone === 'warn' ? '#b84708' : tone === 'ok' ? '#176b2c' : '#4a4d5a';
  const bg =
    tone === 'warn' ? '#fff4ea' : tone === 'ok' ? '#eefbf0' : '#f7f7fb';

  return (
    <div
      style={{
        border: '1px solid #ececf2',
        borderRadius: 12,
        padding: 12,
        background: bg,
        minWidth: 140,
      }}
    >
      <div style={{ fontSize: 12, color: '#6f7484', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

function SampleTable({ items, kind }: { items: AdminProviderLeadsImportPreviewSample[]; kind: ImportKind }) {
  return (
    <div className="table-scroll">
      <table className="table" style={{ minWidth: kind === 'pixel' ? 900 : 700 }}>
        <thead>
          <tr>
            <th>Строка XLSX</th>
            <th>VID</th>
            <th>{kind === 'pixel' ? 'Домен' : 'Проект'}</th>
            <th>Телефон</th>
            {kind === 'pixel' ? <th>Referer</th> : <th>Subdomain</th>}
            <th>Примечание</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, idx) => (
            <tr key={`${item.vid || 'no-vid'}-${idx}`}>
              <td>{item.xlsxRowNumber ?? '—'}</td>
              <td>{item.vid || '—'}</td>
              <td>
                {kind === 'pixel'
                  ? item.domain || item.projectName || '—'
                  : item.projectName
                    ? formatProjectNameForDisplay(item.projectName)
                    : '—'}
              </td>
              <td>{item.phone || '—'}</td>
              <td>{kind === 'pixel' ? item.pixelUrl || '—' : item.subdomain || '—'}</td>
              <td>{item.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ImportCard({ config }: { config: ImportCardConfig }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<AdminProviderLeadsImportPreviewResp | null>(null);
  const [importResult, setImportResult] = useState<AdminProviderLeadsImportCommitResp | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [loadingImport, setLoadingImport] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canImport = useMemo(() => {
    if (!preview) return false;
    if (preview.readyToImport <= 0) return false;
    if (config.kind === 'provider') {
      if (preview.notFoundProjects > 0) return false;
      if (preview.ambiguousProjects > 0) return false;
    }
    return true;
  }, [config.kind, preview]);

  const sampleEntries = useMemo(() => {
    if (!preview) return [];
    return Object.entries(preview.samples || {}).filter(([, items]) => Array.isArray(items) && items.length > 0);
  }, [preview]);

  async function handlePreview() {
    if (!file) {
      setError('Сначала выбери XLSX-файл.');
      return;
    }
    try {
      setLoadingPreview(true);
      setError(null);
      setImportResult(null);
      const result = await config.previewFile(file);
      setPreview(result);
    } catch (err: unknown) {
      console.error(err);
      setPreview(null);
      setImportResult(null);
      setError(getErrorMessage(err, 'Не удалось выполнить preview импорта.'));
    } finally {
      setLoadingPreview(false);
    }
  }

  async function handleImport() {
    if (!preview?.previewId) {
      setError('Сначала выполни preview файла.');
      return;
    }
    if (!canImport) {
      setError('Импорт заблокирован: сначала устрани проблемы из preview.');
      return;
    }
    try {
      setLoadingImport(true);
      setError(null);
      const result = await config.commitPreview(preview.previewId);
      setImportResult(result);
    } catch (err: unknown) {
      console.error(err);
      setImportResult(null);
      setError(getErrorMessage(err, 'Не удалось выполнить импорт.'));
    } finally {
      setLoadingImport(false);
    }
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div
        className="provider-import-card"
        style={{
          border: '1px solid #ececf2',
          borderRadius: 12,
          padding: 16,
          background: '#fff',
          display: 'grid',
          gap: 12,
        }}
      >
        <div>
          <div style={{ fontWeight: 700 }}>{config.title}</div>
          <div className="sub" style={{ marginTop: 4, maxWidth: 920 }}>
            {config.description}
          </div>
          <div className="sub" style={{ marginTop: 4, maxWidth: 920 }}>
            {config.requirements}
          </div>
        </div>

        <div className="provider-import-controls">
          <input
            className="provider-import-file-input"
            type="file"
            accept=".xlsx"
            onChange={(e) => {
              const nextFile = e.target.files?.[0] || null;
              setFile(nextFile);
              setPreview(null);
              setImportResult(null);
              setError(null);
            }}
          />
          <button
            type="button"
            className="btn btn--primary provider-import-action"
            disabled={!file || loadingPreview}
            onClick={() => {
              void handlePreview();
            }}
          >
            {loadingPreview ? 'Проверяем…' : 'Проверить файл'}
          </button>
          <button
            type="button"
            className="btn btn--secondary provider-import-action"
            disabled={!canImport || loadingImport}
            onClick={() => {
              void handleImport();
            }}
          >
            {loadingImport ? 'Импортируем…' : 'Импортировать'}
          </button>
        </div>

        <div className="sub">
          {file ? `Выбран файл: ${file.name}` : 'Файл ещё не выбран.'}
        </div>

        {error && (
          <div style={{ color: '#b42318', background: '#fff1f3', borderRadius: 10, padding: 12 }}>
            {error}
          </div>
        )}

        {importResult && (
          <div style={{ color: '#176b2c', background: '#eefbf0', borderRadius: 10, padding: 12 }}>
            Импорт завершён. Добавлено строк: {importResult.insertedRows}. Пропущено как новые дубли в БД:{' '}
            {importResult.skippedDuplicatesInDb}.
          </div>
        )}
      </div>

        {preview && (
          <>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                gap: 12,
              }}
            >
              <SummaryItem label="Всего строк" value={preview.totalRows} />
              <SummaryItem label="Валидных строк" value={preview.validRows} tone="ok" />
              <SummaryItem label="Ошибки в строках" value={preview.rowsWithErrors} tone={preview.rowsWithErrors > 0 ? 'warn' : 'ok'} />
              <SummaryItem label="Дубли в файле" value={preview.duplicatesInFile} tone={preview.duplicatesInFile > 0 ? 'warn' : 'ok'} />
              <SummaryItem label="Дубли в БД" value={preview.duplicatesInDb} tone={preview.duplicatesInDb > 0 ? 'warn' : 'default'} />
              <SummaryItem label="Новые строки" value={preview.newRows} tone="ok" />
              <SummaryItem label="Готово к импорту" value={preview.readyToImport} tone="ok" />
              <SummaryItem label="Сматчено проектов" value={preview.matchedProjects} tone="ok" />
              <SummaryItem label="Проект не найден" value={preview.notFoundProjects} tone={preview.notFoundProjects > 0 ? 'warn' : 'ok'} />
              <SummaryItem label="Неоднозначных проектов" value={preview.ambiguousProjects} tone={preview.ambiguousProjects > 0 ? 'warn' : 'ok'} />
            </div>

            <div
              style={{
                border: '1px solid #ececf2',
                borderRadius: 12,
                padding: 16,
                background: '#fff',
                display: 'grid',
                gap: 10,
              }}
            >
              <div style={{ fontWeight: 600 }}>Результат preview</div>
              <div className="sub">Файл: {preview.fileName}</div>
              <div className="sub">Preview ID: {preview.previewId}</div>
              {!canImport && (
                <div style={{ color: '#8a4b00' }}>
                  {config.kind === 'provider'
                    ? 'Кнопка импорта заблокирована, пока есть неразрешённые проблемы с проектами или нет строк для импорта.'
                    : 'Кнопка импорта заблокирована, потому что нет строк, готовых к импорту.'}
                </div>
              )}
              {config.kind === 'pixel' && (preview.notFoundProjects > 0 || preview.ambiguousProjects > 0) && (
                <div style={{ color: '#8a4b00' }}>
                  Строки с ненайденным или неоднозначным Pixel-доменом будут пропущены. Готовые строки можно импортировать.
                </div>
              )}
            </div>

            {Object.keys(preview.errorsBreakdown || {}).length > 0 && (
              <div
                style={{
                  border: '1px solid #ececf2',
                  borderRadius: 12,
                  padding: 16,
                  background: '#fff',
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 12 }}>Ошибки валидации</div>
                <div style={{ display: 'grid', gap: 8 }}>
                  {Object.entries(preview.errorsBreakdown).map(([key, value]) => (
                    <div key={key} className="sub">
                      {key}: {value}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {sampleEntries.map(([groupKey, items]) => (
              <div key={groupKey} className="table-card">
                <div className="table-toolbar">
                  <div style={{ fontWeight: 600 }}>{SAMPLE_LABELS[groupKey] || groupKey}</div>
                  <div className="sub">Показаны первые {items.length} строк</div>
                </div>
                <SampleTable items={items} kind={config.kind} />
              </div>
            ))}
          </>
        )}
    </div>
  );
}

const IMPORT_CONFIGS: ImportCardConfig[] = [
  {
    kind: 'provider',
    title: 'Обычные данные provider',
    description:
      'Для стандартной выгрузки поставщика. Preview проверяет файл без записи в БД, commit записывает только после подтверждения.',
    requirements: 'Ожидаемые колонки: id, Проект, Телефон, Создано, Комментарий.',
    previewFile: previewAdminProviderLeadsImport,
    commitPreview: commitAdminProviderLeadsImport,
  },
  {
    kind: 'pixel',
    title: 'Данные Пикселя',
    description:
      'Для Pixel-выгрузки. Проект ищется по домену среди проектов с источником «Пиксель», дата в ЛК будет моментом импорта.',
    requirements: 'Ожидаемые колонки: id, Domain, Phone, Created, Referer. Если в Phone окажется несколько номеров, берётся первый.',
    previewFile: previewAdminPixelLeadsImport,
    commitPreview: commitAdminPixelLeadsImport,
  },
];

function AdminProviderLeadsImport() {
  return (
    <div className="table-card">
      <div className="table-toolbar toolbar-split">
        <div className="toolbar-left" style={{ alignItems: 'flex-start', flexDirection: 'column' }}>
          <div style={{ fontWeight: 600 }}>Ручной импорт лидов из XLSX</div>
          <div className="sub" style={{ maxWidth: 920 }}>
            Экран доступен только администратору. Сначала выполняется preview файла без записи в БД,
            затем отдельным действием подтверждается импорт.
          </div>
        </div>
      </div>

      <div style={{ padding: 16, display: 'grid', gap: 20 }}>
        {IMPORT_CONFIGS.map((config) => (
          <ImportCard key={config.kind} config={config} />
        ))}
      </div>
    </div>
  );
}

export default AdminProviderLeadsImport;
