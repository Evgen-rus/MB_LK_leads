// Корневой лейаут приложения: шапка, левое меню и область контента с таблицей
import './App.css';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import ProjectsTable from './components/ProjectsTable';

function App() {
  return (
    <div className="layout">
      <Header />
      <div className="content">
        <Sidebar />
        <main className="main">
          <div className="page-title">Проекты</div>
          <ProjectsTable />
        </main>
      </div>
    </div>
  );
}

export default App;
