// Корневой лейаут приложения: шапка, левое меню и область контента с таблицей
import './App.css';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import ProjectsTable from './components/ProjectsTable';
import { useMemo, useState } from 'react';
import CreateProjectModal from './components/CreateProjectModal';
import EditProjectModal from './components/EditProjectModal';
import { projects as initialProjects } from './data/projects';
import type { Project } from './types/project';

function App() {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [rows, setRows] = useState<Project[]>(initialProjects);
  const [editing, setEditing] = useState<Project | null>(null);

  const maxId = useMemo(() => {
    return rows.length ? Math.max(...rows.map((r) => r.id)) : 0;
  }, [rows]);

  return (
    <div className="layout">
      <Header onCreateClick={() => setIsCreateOpen(true)} />
      <div className="content">
        <Sidebar />
        <main className="main">
          <div className="page-title">Проекты</div>
          <ProjectsTable
            rows={rows}
            onDelete={(ids) => {
              if (!ids.length) return;
              setRows((prev) => prev.filter((p) => !ids.includes(p.id)));
            }}
            onEdit={(row) => setEditing(row)}
          />
        </main>
      </div>
      {isCreateOpen && (
        <CreateProjectModal
          onClose={() => setIsCreateOpen(false)}
          onSubmit={(items) => {
            setRows((prev) => {
              let nextId = maxId + 1;
              const createdAt = new Date().toISOString().slice(0, 10);
              const newProjects: Project[] = items.map((it) => {
                const daysReceived = it.days.length ? it.days.map(d => `${d}.`).join(' ').trim() : '';
                const sourcesCount = (it.sites?.length || 0) + (it.phones?.length || 0) + (it.smsSenderName ? 1 : 0);
                const p: Project = {
                  id: nextId++,
                  status: it.status,
                  deliveryStatus: 'На модерации',
                  name: it.name,
                  tag: it.tag,
                  collectionSource: it.collectionSource,
                  dataSourceCode: it.dataSourceCode,
                  regionMode: it.regionMode,
                  regions: it.regions,
                  sites: it.sites,
                  phones: it.phones,
                  smsSenderName: it.smsSenderName,
                  dataLimit: it.dataLimit,
                  numbersToday: 0,
                  numbersTotal: 0,
                  daysReceived,
                  sourcesCount,
                  createdAt,
                };
                return p;
              });
              return [...newProjects, ...prev];
            });
          }}
        />
      )}
      {editing && (
        <EditProjectModal
          project={editing}
          onClose={() => setEditing(null)}
          onSubmit={(u) => {
            setRows((prev) => prev.map(p => {
              if (p.id !== editing.id) return p;
              const daysReceived = u.days.length ? u.days.map(d => `${d}.`).join(' ').trim() : p.daysReceived;
              const sourcesCount = (u.sites?.length || 0) + (u.phones?.length || 0) + (u.smsSenderName ? 1 : 0);
              return {
                ...p,
                name: u.name,
                tag: u.tag,
                status: u.status,
                dataLimit: u.dataLimit,
                regionMode: u.regionMode,
                regions: u.regions,
                sites: u.sites,
                phones: u.phones,
                smsSenderName: u.smsSenderName,
                daysReceived,
                sourcesCount,
              };
            }));
          }}
        />
      )}
    </div>
  );
}

export default App;
