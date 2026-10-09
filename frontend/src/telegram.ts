interface TelegramApp {
  initData: string;
  themeParams?: Record<string, string>;
  safeAreaInset?: { top: number; right: number; bottom: number; left: number };
  contentSafeAreaInset?: { top: number; right: number; bottom: number; left: number };
  ready: () => void;
  expand: () => void;
  enableClosingConfirmation?: () => void;
  onEvent: (event: string, handler: () => void) => void;
}
declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramApp };
  }
}
export function telegramData(): string {
  return window.Telegram?.WebApp?.initData ?? '';
}
export function initializeTelegram(): void {
  const app = window.Telegram?.WebApp;
  if (!app?.initData) return;
  const refresh = () => {
    const theme = app.themeParams ?? {};
    for (const [target, source] of [
      ['--app-bg', 'bg_color'],
      ['--app-text', 'text_color'],
      ['--app-panel', 'secondary_bg_color'],
      ['--app-hint', 'hint_color'],
      ['--app-button', 'button_color'],
      ['--app-button-text', 'button_text_color'],
    ]) {
      const value = theme[source];
      if (value && /^#[a-f0-9]{6}$/i.test(value))
        document.documentElement.style.setProperty(target, value);
    }
    for (const side of ['top', 'right', 'bottom', 'left'] as const) {
      const inset =
        Math.max(0, app.safeAreaInset?.[side] ?? 0) +
        Math.max(0, app.contentSafeAreaInset?.[side] ?? 0);
      document.documentElement.style.setProperty(`--safe-${side}`, `${Math.min(inset, 200)}px`);
    }
  };
  refresh();
  for (const event of ['themeChanged', 'safeAreaChanged', 'contentSafeAreaChanged'])
    app.onEvent(event, refresh);
  app.ready();
  app.expand();
  app.enableClosingConfirmation?.();
}
