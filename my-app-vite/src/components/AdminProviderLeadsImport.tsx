import { useMemo, useState } from 'react';
import {
  commitAdminProviderLeadsImport,
  previewAdminProviderLeadsImport,
  type AdminProviderLeadsImportCommitResp,
  type AdminProviderLeadsImportPreviewResp,
  type AdminProviderLeadsImportPreviewSample,
} from '../api';

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
  notFoundProjects: 'Проекты не найдены',
  ambiguousProjects: 'Неоднозначные проекты',
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

function SampleTable({ items }: { items: AdminProviderLeadsImportPreviewSample[] }) {
  return (
    <div className="table-scroll">
      <table className="table" style={{ minWidth: 700 }}>
        <thead>
          <tr>
            <th>Строка XLSX</th>
            <th>VID</th>
            <th>Проект</th>
            <th>Телефон</th>
            <th>Subdomain</th>
            <th>Примечание</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, idx) => (
            <tr key={`${item.vid || 'no-vid'}-${idx}`}>
              <td>{item.xlsxRowNumber ?? '—'}</td>
              <td>{item.vid || '—'}</td>
              <td>{item.projectName || '—'}</td>
              <td>{item.phone || '—'}</td>
              <td>{item.subdomain || '—'}</td>
              <td>{item.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AdminProviderLeadsImport() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<AdminProviderLeadsImportPreviewResp | null>(null);
  const [importResult, setImportResult] = useState<AdminProviderLeadsImportCommitResp | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [loadingImport, setLoadingImport] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canImport = useMemo(() => {
    if (!preview) return false;
    if (preview.readyToImport <= 0) return false;
    if (preview.notFoundProjects > 0) return false;
    if (preview.ambiguousProjects > 0) return false;
    return true;
  }, [preview]);

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
      const result = await previewAdminProviderLeadsImport(file);
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
      const result = await commitAdminProviderLeadsImport(preview.previewId);
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
    <div className="table-card">
      <div className="table-toolbar toolbar-split">
        <div className="toolbar-left" style={{ alignItems: 'flex-start', flexDirection: 'column' }}>
          <div style={{ fontWeight: 600 }}>Ручной импорт лидов из XLSX</div>
          <div className="sub" style={{ maxWidth: 820 }}>
            Экран доступен только администратору. Сначала выполняется preview файла без записи в БД,
            затем отдельным действием подтверждается импорт.
          </div>
        </div>
      </div>

      <div style={{ padding: 16, display: 'grid', gap: 16 }}>
        <div
          style={{
            border: '1px solid #ececf2',
            borderRadius: 12,
            padding: 16,
            background: '#fff',
            display: 'grid',
            gap: 12,
          }}
        >
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <input
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
              className="btn btn--primary"
              disabled={!file || loadingPreview}
              onClick={() => {
                void handlePreview();
              }}
            >
              {loadingPreview ? 'Проверяем…' : 'Проверить файл'}
            </button>
            <button
              type="button"
              className="btn btn--secondary"
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
                  Кнопка импорта заблокирована, пока есть неразрешённые проблемы с проектами или нет строк для импорта.
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
                <SampleTable items={items} />
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

export default AdminProviderLeadsImport;
