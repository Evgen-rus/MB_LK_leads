## Как вернуть виджет чата Bitrix24

Код виджета и его подключение убраны из проекта. Ниже краткая инструкция, как вернуть его при необходимости.

1. **Создайте файл компонента `ChatWidget.tsx`** в папке `my-app-vite/src/components/` со следующим содержимым:

```tsx
import { useEffect } from 'react';

function ChatWidget() {
  useEffect(() => {
    // Если скрипт уже есть на странице — ничего не делаем
    if (document.querySelector('script[src*="bitrix24"]')) {
      return;
    }

    // URL виджета должен быть задан в переменной окружения VITE_BITRIX24_WIDGET_URL
    const bitrixScriptUrl = (import.meta as any).env?.VITE_BITRIX24_WIDGET_URL as string | undefined;
    if (!bitrixScriptUrl) {
      return;
    }

    const script = document.createElement('script');
    script.async = true;
    script.src = `${bitrixScriptUrl}?${Math.floor(Date.now() / 60000)}`;

    const firstScript = document.getElementsByTagName('script')[0];
    if (firstScript && firstScript.parentNode) {
      firstScript.parentNode.insertBefore(script, firstScript);
    } else {
      document.head.appendChild(script);
    }
  }, []);

  // Компонент ничего не рисует — виджет сам добавит кнопку на страницу
  return null;
}

export default ChatWidget;
```

2. **Подключите компонент в `my-app-vite/src/App.tsx`:**

- Добавьте импорт рядом с другими компонентами:

```tsx
import ChatWidget from './components/ChatWidget';
```

- В JSX-разметке внизу (после блока `<div className="content">...</div>`) добавьте вызов компонента:

```tsx
    </div>
    <ChatWidget />
    {isCreateOpen && (
      {/* остальные модальные окна */}
```

3. **Укажите URL скрипта виджета в переменных окружения:**

- Для разработки (локально) добавьте в корневой `.env` или `.env.local` строку:

```env
VITE_BITRIX24_WIDGET_URL=https://example.bitrix24.ru/bitrix/js/crm/site_button/loader_XXX.js
```

- Для продакшена добавьте ту же переменную в файл окружения, который использует Vite при сборке (например, `my-app-vite/.env.production`).

4. **Пересоберите фронтенд и задеплойте:**

```bash
cd my-app-vite
npm run build
```

После сборки и деплоя на продакшене виджет Bitrix24 снова появится на страницах приложения.


