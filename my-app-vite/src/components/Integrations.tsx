// Простая страница-заглушка для раздела "Интеграции"
// Показывает сообщение: для подключения CRM обратитесь в поддержку

function Integrations() {
  return (
    <div className="card" style={{ padding: 16 }}>
      <h3 style={{ marginTop: 0, marginBottom: 12 }}>Интеграции</h3>
      <p style={{ margin: 0, lineHeight: 1.6 }}>
        Для подключения интеграций (Bitrix24, amoCRM и др.) обратитесь в чат поддержки.
        Мы подскажем оптимальную схему и поможем с настройкой.
      </p>
      <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
        <button className="btn btn--primary" onClick={() => window.dispatchEvent(new CustomEvent('open-support-chat'))}>
          Открыть чат поддержки
        </button>
      </div>
    </div>
  );
}

export default Integrations;


