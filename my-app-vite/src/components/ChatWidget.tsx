import { useEffect } from 'react';

/**
 * Компонент для интеграции чата техподдержки Битрикс24.
 * Загружает скрипт виджета Битрикс24, который автоматически создаст кнопку чата на странице.
 * 
 * URL скрипта настраивается через переменную окружения VITE_BITRIX24_WIDGET_URL
 * ВАЖНО: мы настроили Vite (envDir) так, чтобы он читал переменные из корневого .env
 */
function ChatWidget() {
  useEffect(() => {
    // Проверяем, не загружен ли уже скрипт Битрикс24
    if (document.querySelector('script[src*="bitrix24"]')) {
      return;
    }

    // URL скрипта Битрикс24 берём ТОЛЬКО из env (без дефолта)
    // В корневом .env добавьте: VITE_BITRIX24_WIDGET_URL=https://.../loader_XXX.js
    const bitrixScriptUrl = (import.meta as any).env?.VITE_BITRIX24_WIDGET_URL as string | undefined;
    
    // Если URL не задан, не загружаем виджет
    if (!bitrixScriptUrl) {
      console.warn('Bitrix24 widget URL not configured');
      return;
    }

    // Создаём и загружаем скрипт
    const script = document.createElement('script');
    script.async = true;
    script.src = `${bitrixScriptUrl}?${Math.floor(Date.now() / 60000)}`;
    
    // Добавляем скрипт в head документа
    const firstScript = document.getElementsByTagName('script')[0];
    if (firstScript && firstScript.parentNode) {
      firstScript.parentNode.insertBefore(script, firstScript);
    } else {
      document.head.appendChild(script);
    }

    // Очистка при размонтировании компонента (опционально)
    return () => {
      // Обычно скрипт Битрикс24 остаётся на странице, но можно удалить при необходимости
      // const existingScript = document.querySelector(`script[src*="${bitrixScriptUrl}"]`);
      // if (existingScript) {
      //   existingScript.remove();
      // }
    };
  }, []);


  // Компонент не рендерит ничего видимого - Битрикс24 сам создаст кнопку чата
  return null;
}

export default ChatWidget;


