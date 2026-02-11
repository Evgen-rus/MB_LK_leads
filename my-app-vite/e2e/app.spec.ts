/**
 * E2E-тесты: формы, воркфлоу, responsive, консоль
 *
 * Запуск: npm run test:e2e (из my-app-vite)
 * Требуется: бэкенд на :8000, фронт на :5173 (или webServer поднимет сам)
 *
 * Тестовые учётные данные (опционально): E2E_LOGIN, E2E_PASSWORD
 */
/// <reference types="node" />
import { test, expect } from '@playwright/test';

test.describe('Форма логина', () => {
  test('отображает поля логин и пароль', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Вход' })).toBeVisible();
    await expect(page.getByLabel(/логин/i)).toBeVisible();
    await expect(page.getByLabel(/пароль/i)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Войти' })).toBeVisible();
  });

  test('кнопка Войти отключена при пустых полях', async ({ page }) => {
    await page.goto('/');
    const submitBtn = page.getByRole('button', { name: 'Войти' });
    await expect(submitBtn).toBeDisabled();
  });

  test('кнопка Войти отключена при только логине', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel(/логин/i).fill('testuser');
    const submitBtn = page.getByRole('button', { name: 'Войти' });
    await expect(submitBtn).toBeDisabled();
  });

  test('кнопка Войти активна при заполненных полях', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel(/логин/i).fill('testuser');
    await page.getByLabel(/пароль/i).fill('testpass');
    const submitBtn = page.getByRole('button', { name: 'Войти' });
    await expect(submitBtn).toBeEnabled();
  });

  test('показывает ошибку при неверных учётных данных', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel(/логин/i).fill('wrong_user_12345');
    await page.getByLabel(/пароль/i).fill('wrong_password_xyz');
    await page.getByRole('button', { name: 'Войти' }).click();
    // Ожидаем сообщение об ошибке (неверный логин или пароль)
    await expect(page.getByText(/неверный логин или пароль/i)).toBeVisible({
      timeout: 10000,
    });
  });

  test('успешный вход с корректными данными (если заданы E2E_LOGIN/E2E_PASSWORD)', async ({
    page,
  }) => {
    const login = process.env.E2E_LOGIN;
    const password = process.env.E2E_PASSWORD;
    if (!login || !password) {
      test.skip();
      return;
    }
    await page.goto('/');
    await page.getByLabel(/логин/i).fill(login);
    await page.getByLabel(/пароль/i).fill(password);
    await page.getByRole('button', { name: 'Войти' }).click();
    // После успешного входа — появляется главный контент (Проекты или Идентификации)
    await expect(
      page.getByRole('button', { name: 'Выйти' })
    ).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Мониторинг консоли (JS-ошибки)', () => {
  test('нет JavaScript-ошибок при загрузке страницы логина', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      const type = msg.type();
      if (type === 'error') {
        const text = msg.text();
        consoleErrors.push(text);
      }
    });
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    // Игнорируем типичные ошибки от внешних скриптов/расширений
    const criticalErrors = consoleErrors.filter(
      (t) =>
        !t.includes('favicon') &&
        !t.includes('extension') &&
        !t.includes('chrome-extension')
    );
    expect(criticalErrors).toHaveLength(0);
  });
});

test.describe('Responsive: мобильный viewport', () => {
  test('форма логина отображается на мобильной ширине', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Вход' })).toBeVisible();
    await expect(page.getByLabel(/логин/i)).toBeVisible();
    await expect(page.getByLabel(/пароль/i)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Войти' })).toBeVisible();
  });

  test('форма логина работает на планшетной ширине', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('/');
    await page.getByLabel(/логин/i).fill('test');
    await page.getByLabel(/пароль/i).fill('test');
    const submitBtn = page.getByRole('button', { name: 'Войти' });
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();
    await expect(page.getByText(/неверный логин или пароль/i)).toBeVisible({
      timeout: 10000,
    });
  });
});

test.describe('Воркфлоу после входа (если есть тестовые учётные данные)', () => {
  test.beforeEach(async ({ page }) => {
    const login = process.env.E2E_LOGIN;
    const password = process.env.E2E_PASSWORD;
    if (!login || !password) {
      test.skip();
      return;
    }
    await page.goto('/');
    await page.getByLabel(/логин/i).fill(login);
    await page.getByLabel(/пароль/i).fill(password);
    await page.getByRole('button', { name: 'Войти' }).click();
    await expect(page.getByRole('button', { name: 'Выйти' })).toBeVisible({
      timeout: 10000,
    });
  });

  test('клик по вкладкам sidebar — без JS-ошибок', async ({ page }) => {
    const login = process.env.E2E_LOGIN;
    const password = process.env.E2E_PASSWORD;
    if (!login || !password) {
      test.skip();
      return;
    }

    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      }
    });

    // Sidebar использует <li> с onClick, не ссылки
    // Идентификации
    const leadsItem = page.getByText('Идентификации');
    if (await leadsItem.isVisible()) {
      await leadsItem.click();
      await page.waitForLoadState('networkidle');
    }

    // Проекты
    const projectsItem = page.getByText('Проекты');
    if (await projectsItem.isVisible()) {
      await projectsItem.click();
      await page.waitForLoadState('networkidle');
    }

    // Отчеты (в Sidebar текст "Отчеты")
    const reportsItem = page.getByText('Отчеты');
    if (await reportsItem.isVisible()) {
      await reportsItem.click();
      await page.waitForLoadState('networkidle');
    }

    const criticalErrors = consoleErrors.filter(
      (t) => !t.includes('favicon') && !t.includes('extension')
    );
    expect(criticalErrors).toHaveLength(0);
  });

  test('заполнение формы создания проекта тестовыми данными', async ({ page }) => {
    const login = process.env.E2E_LOGIN;
    const password = process.env.E2E_PASSWORD;
    if (!login || !password) {
      test.skip();
      return;
    }

    // Переходим в «Проекты» (после входа дефолт — Идентификации)
    await page.getByText('Проекты').click();
    await page.waitForLoadState('networkidle');

    // Открываем модалку создания проекта
    await page.getByRole('button', { name: /добавить проект/i }).click();
    await expect(page.getByText('Создать проект')).toBeVisible({ timeout: 5000 });

    // Заполняем поле названия
    const nameInput = page.getByPlaceholder('Название проекта');
    await nameInput.fill('E2E Test Project');

    // Заполняем лимит (в модалке несколько input[type="number"] для разных B-кодов)
    const limitInputs = page.locator('input[type="number"]');
    if ((await limitInputs.count()) > 0) {
      await limitInputs.first().fill('50');
    }

    // Проверяем, что данные отобразились
    await expect(nameInput).toHaveValue('E2E Test Project');
  });
});
