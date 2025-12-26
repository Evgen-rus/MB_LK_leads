import { useEffect, useState, useCallback } from 'react';
import AddPhonesModal from './AddPhonesModal';
import { addToBlacklist, deleteFromBlacklist, listBlacklist, type BlacklistPhone } from '../api';

function Blacklist() {
  const [rows, setRows] = useState<BlacklistPhone[]>([]);
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  // Отвязываем от state page/pageSize, чтобы пагинация не сбрасывала данные на первую страницу
  const fetchPage = useCallback(async (nextPage = page, nextPageSize = pageSize, q = search) => {
    try {
      setLoading(true);
      const offset = (nextPage - 1) * nextPageSize;
      const resp = await listBlacklist({ offset, limit: nextPageSize, q: q.trim() || undefined });
      setRows(resp.items);
      setTotal(resp.total);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [pageSize, search]);

  useEffect(() => {
    fetchPage(1); // первичная загрузка
  }, [fetchPage]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <input
            type="search"
            placeholder="Найти телефон"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                setPage(1);
                fetchPage(1, pageSize, (e.target as HTMLInputElement).value);
              }
            }}
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
            ) : rows.length === 0 ? (
              <tr><td className="muted" colSpan={3}>Список пуст</td></tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id}>
                  <td className="name" style={{ whiteSpace: 'nowrap' }}>{r.phone}</td>
                  <td className="muted">{r.createdAt}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      className="icon-btn"
                      title="Удалить"
                      onClick={async () => {
                        try {
                          await deleteFromBlacklist(r.id);
                          // после удаления перезагрузим текущую страницу (с поправкой, если стала пустой)
                          const nextCount = rows.length - 1;
                          const nextPage = nextCount === 0 && page > 1 ? page - 1 : page;
                          setPage(nextPage);
                          await fetchPage(nextPage);
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
        Показано {rows.length} из {total}
        <div className="spacer" />
        <div className="pager">
          <button className="pager__btn" disabled={page <= 1} onClick={() => { const p = Math.max(1, page - 1); setPage(p); fetchPage(p); }}>‹</button>
          <span className="pager__info">{page} / {totalPages}</span>
          <button className="pager__btn" disabled={page >= totalPages} onClick={() => { const p = Math.min(totalPages, page + 1); setPage(p); fetchPage(p); }}>›</button>
          <select className="pager__size" value={pageSize} onChange={(e) => { const s = Number(e.target.value); setPageSize(s); setPage(1); fetchPage(1, s); }}>
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      {isAddOpen && (
        <AddPhonesModal
          onClose={() => setIsAddOpen(false)}
          onSubmit={async (phones) => {
            try {
              const created = await addToBlacklist(phones);
              // если первая страница — подмешаем для мгновенного эффекта, иначе просто перезагрузим текущую
              if (page === 1) {
                setRows(prev => [...created, ...prev].slice(0, pageSize));
                setTotal(prev => prev + created.length);
              } else {
                await fetchPage(page);
              }
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


