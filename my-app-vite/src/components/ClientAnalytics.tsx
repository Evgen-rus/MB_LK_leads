import { useEffect, useState } from "react";
import { downloadExport, fetchClientExports } from "./leadAnalytics/api";
import { ResultViewer } from "./leadAnalytics/ResultViewer";
import type { ClientExportRecord } from "./leadAnalytics/types";
import "./leadAnalytics/styles.css";

export default function ClientAnalytics() {
  const [reports, setReports] = useState<ClientExportRecord[]>([]);
  const [selected, setSelected] = useState<ClientExportRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    fetchClientExports()
      .then((items) => { if (active) setReports(items); })
      .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : "Не удалось загрузить аналитику"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  async function download(report: ClientExportRecord) {
    setError("");
    setLoading(true);
    try {
      await downloadExport(report.group_id, report.id, `analytics-${report.id}.xlsx`, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось скачать Excel");
    } finally {
      setLoading(false);
    }
  }

  return <section className={`lead-analytics ${selected ? "isResultViewing" : ""}`} aria-label="Аналитика">
    {error && <div className="alert" role="alert">{error}</div>}
    {selected ? <ResultViewer
      key={`${selected.group_id}:${selected.id}`}
      groupName={selected.group_name}
      groupId={selected.group_id}
      exportId={selected.id}
      client
      canStartNew={false}
      canDownload={selected.report_available && !loading}
      onDownload={() => void download(selected)}
      onBack={() => { setSelected(null); setRefresh((value) => value + 1); }}
    /> : <section className="panel historyPanel">
      <div className="panelHeader">
        <div><h2>Аналитика</h2><p>Готовые отчёты, опубликованные для вас.</p></div>
        <button type="button" className="ghostButton" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>Обновить</button>
      </div>
      {loading ? <p role="status">Загружаем аналитику…</p> : error ? null : reports.length === 0 ? <div className="emptyState">
        <strong>Аналитика пока не опубликована</strong><span>Здесь появятся подготовленные для вас отчёты.</span>
      </div> : <div className="tableWrap historyWrap clientAnalyticsHistory"><table>
        <thead><tr><th>Группа проектов</th><th>Период</th><th>Дата анализа</th><th>Действия</th></tr></thead>
        <tbody>{reports.map((report) => <tr key={report.id}>
          <td>{report.group_name} · №{report.export_number}</td>
          <td data-label="Период">{report.period_start} — {report.period_end}</td>
          <td data-label="Дата анализа">{report.analysis_date}</td>
          <td><div className="actions">
            <button type="button" className="ghostButton" onClick={() => { setError(""); setSelected(report); }}>Открыть аналитику</button>
            {report.report_available && <button type="button" className="download secondary" onClick={() => void download(report)}>Excel</button>}
          </div></td>
        </tr>)}</tbody>
      </table></div>}
    </section>}
  </section>;
}
