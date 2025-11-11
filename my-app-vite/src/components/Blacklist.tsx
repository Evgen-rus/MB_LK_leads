import { useEffect, useMemo, useState } from 'react';
import AddPhonesModal from './AddPhonesModal';
import { addToBlacklist, deleteFromBlacklist, listBlacklist, type BlacklistPhone } from '../api';

function Blacklist() {
  const [rows, setRows] = useState<BlacklistPhone[]>([]);
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const data = await listBlacklist();
        setRows(data);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim();
    if (!q) return rows;
    return rows.filter(r => r.phone.includes(q));
  }, [rows, search]);

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <input
            type="search"
            placeholder="Найти телефон"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="actions">
          <button className="btn btn--primary" onClick={() => setIsAddOpen(true)}>+ Добавить телефоны</button>
        </div>
      </div>

      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Телефон</th>
              <th>Дата добавления</th>
              <th style={{ width: 56 }}></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td className="muted" colSpan={3}>Загрузка...</td></tr>
            ) : filtered.length === 0 ? (
              <tr><td className="muted" colSpan={3}>Список пуст</td></tr>
            ) : (
              filtered.map((r, index) => (
                <tr key={r.id} className={index % 2 === 0 ? 'row-alt' : ''}>
                  <td className="name" style={{ whiteSpace: 'nowrap' }}>{r.phone}</td>
                  <td className="muted">{r.createdAt}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      className="icon-btn"
                      title="Удалить"
                      onClick={async () => {
                        try {
                          await deleteFromBlacklist(r.id);
                          setRows(prev => prev.filter(x => x.id !== r.id));
                        } catch (e) {
                          console.error(e);
                        }
                      }}
                    >
                      🗑️
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="table-footer">
        Показано {filtered.length} из {rows.length}
        <div className="spacer" />
        <div>
          <button className="btn btn--ghost">1</button>
          <select defaultValue={50}>
            <option>10</option>
            <option>25</option>
            <option>50</option>
          </select>
        </div>
      </div>

      {isAddOpen && (
        <AddPhonesModal
          onClose={() => setIsAddOpen(false)}
          onSubmit={async (phones) => {
            try {
              const created = await addToBlacklist(phones);
              setRows(prev => [...created, ...prev]);
              setIsAddOpen(false);
            } catch (e) {
              console.error(e);
            }
          }}
        />
      )}
    </div>
  );
}

export default Blacklist;


