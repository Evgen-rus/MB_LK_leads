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
          <div className="page-title">Проекты и каналы</div>
          <ProjectsTable />
        </main>
      </div>
    </div>
  );
}

export default App;


