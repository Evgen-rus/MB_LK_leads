// Верхняя панель (бренд, инфо о менеджере, основная кнопка действий)
type HeaderProps = {
  onCreateClick?: () => void;
};

function Header({ onCreateClick }: HeaderProps) {
  return (
    <header className="header">
      <div className="header__left">
        <div className="brand">Mad Boss <span className="brand__pill">leads</span></div>
        <div className="divider" />
        <div className="header__info">Менеджер: Евгений Расюк</div>
      </div>
      <div className="header__right">
        <button className="btn btn--primary" onClick={onCreateClick}>+ Добавить проект</button>
      </div>
    </header>
  );
}

export default Header;


