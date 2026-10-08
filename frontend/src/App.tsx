import icon from '../../assets/icon.png';

export function App() {
  return (
    <main className="welcome">
      <img className="welcome__icon" src={icon} alt="Крыса-повар Ratatouille" />
      <p className="welcome__eyebrow">Готовим неделю заранее</p>
      <h1>Ratatouille</h1>
      <p className="welcome__description">Меню, покупки и готовка на неделю — в Telegram.</p>
      <p className="welcome__status">Приложение готовится к запуску.</p>
    </main>
  );
}
