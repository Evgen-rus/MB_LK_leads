// Корневой лейаут приложения: шапка, левое меню и область контента с таблицей
import './App.css';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import ProjectsTable from './components/ProjectsTable';
import { useMemo, useState } from 'react';
import CreateProjectModal from './components/CreateProjectModal';
import { projects as initialProjects } from './data/projects';
import type { Project } from './types/project';

function App() {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [rows, setRows] = useState<Project[]>(initialProjects);

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
          />
        </main>
      </div>
      {isCreateOpen && (
        <CreateProjectModal
          onClose={() => setIsCreateOpen(false)}
          onSubmit={(payload) => {
            const newProject: Project = {
              id: maxId + 1,
              status: payload.status,
                  deliveryStatus: 'На модерации',
              name: payload.name,
              tag: payload.tag,
              type: payload.type,
              dataLimit: payload.dataLimit,
              numbersToday: 0,
              numbersTotal: 0,
              daysReceived: 'Пн. Вт. Ср.',
                  sourcesCount: 0,
                  createdAt: new Date().toISOString().slice(0, 10),
            };
            setRows((prev) => [newProject, ...prev]);
          }}
        />
      )}
    </div>
  );
}

export default App;
