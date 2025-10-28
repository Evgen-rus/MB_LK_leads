function Header() {
  return (
    <header className="header">
      <div className="header__left">
        <div className="brand">Mad Boss <span className="brand__pill">leads</span></div>
        <div className="divider" />
        <div className="header__info">Менеджер: Евгений Расюк</div>
      </div>
      <div className="header__right">
        <button className="btn btn--primary">+ Добавить канал</button>
      </div>
    </header>
  );
}

export default Header;


