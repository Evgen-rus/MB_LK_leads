// Корневой лейаут приложения: левое меню и область контента с таблицей
import './App.css';
import Sidebar from './components/Sidebar';
import ProjectsTable from './components/ProjectsTable';
import { useState, useEffect } from 'react';
import CreateProjectModal from './components/CreateProjectModal';
import EditProjectModal from './components/EditProjectModal';
import type { Project } from './types/project';
import { createProjects as apiCreate, fetchProjects as apiList, updateProject as apiUpdate, deleteProject as apiDelete } from './api';

function App() {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [rows, setRows] = useState<Project[]>([]);
  const [editing, setEditing] = useState<Project | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await apiList();
        setRows(data);
      } catch (e) {
        console.error(e);
      }
    })();
  }, []);

  return (
    <div className="layout">
      <div className="content">
        <Sidebar />
        <main className="main">
          <div className="page-title">Проекты</div>
          <ProjectsTable
            rows={rows}
            onCreate={() => setIsCreateOpen(true)}
            onDelete={(ids) => {
              if (!ids.length) return;
              (async () => {
                try {
                  // В UI удаляется по одному, но обработаем массив на будущее
                  await Promise.all(ids.map((id) => apiDelete(id)));
                  setRows((prev) => prev.filter((p) => !ids.includes(p.id)));
                } catch (e) {
                  console.error(e);
                }
              })();
            }}
            onEdit={(row) => setEditing(row)}
          />
        </main>
      </div>
      {isCreateOpen && (
        <CreateProjectModal
          onClose={() => setIsCreateOpen(false)}
          onSubmit={(items) => {
            (async () => {
              try {
                const created = await apiCreate(items);
                setRows((prev) => [...created, ...prev]);
              } catch (e) {
                console.error(e);
              }
            })();
          }}
        />
      )}
      {editing && (
        <EditProjectModal
          project={editing}
          onClose={() => setEditing(null)}
          onSubmit={(u) => {
            (async () => {
              try {
                const updated = await apiUpdate(editing.id, u as any);
                setRows((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
              } catch (e) {
                console.error(e);
              }
            })();
          }}
        />
      )}
    </div>
  );
}

export default App;
