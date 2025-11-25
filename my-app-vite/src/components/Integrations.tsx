// Простая страница-заглушка для раздела "Интеграции"
// Показывает сообщение: для подключения CRM обратитесь в поддержку

function Integrations() {
  return (
    <div className="card" style={{ padding: 16 }}>
      <h3 style={{ marginTop: 0, marginBottom: 12 }}>Интеграции</h3>
      <p style={{ margin: 0, lineHeight: 1.6 }}>
        Для подключения интеграций с CRM (amoCRM и др.) обратитесь в поддержку.
        Мы подскажем оптимальную схему и поможем с настройкой.
      </p>
    </div>
  );
}

export default Integrations;


