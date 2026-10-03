import { expect, test, type Page } from '@playwright/test';

const group = { id: 11, client_id: 2, name: 'Тестовая группа', project_ids: [101, 102], spreadsheet_url: null, archived: false };
const period = { period_start: '2026-01-01', period_end: '2026-01-07', total_count: 10, missed_count: 2, missed_rate: 0.2, quality_count: 4, quality_rate: 0.4, demand_count: 4, demand_rate: 0.4 };
const report = { ...period, id: 5, export_number: 1, report_available: true, analysis_date: '2026-01-08', source_file_name: 'synthetic.xlsx', periods: [period] };

test.afterEach(async ({ page }, info) => {
  if (info.errors.length) console.error(info.errors.map(error => error.message).join('\n'));
  await page.close();
});

async function preparePage(page: Page, role: 'admin' | 'agent' | 'client' = 'admin', savedView = 'education', workflow = false) {
  const payload = { user_id: role === 'admin' ? 1 : 2, role, is_admin: role === 'admin', exp: Math.floor(Date.now() / 1000) + 3600 };
  const token = `synthetic.${Buffer.from(JSON.stringify(payload)).toString('base64url')}.synthetic`;
  await page.addInitScript(({ value, view }) => {
    localStorage.setItem('access_token', value);
    localStorage.setItem('last_view', view);
  }, { value: token, view: savedView });
  const analyticsRequests: string[] = [];
  let analysisFinished = false;
  const lkMapping = { sheet_name: 'leads', date_column: 'Дата', phone_column: 'Телефон', source_column: 'Источники', lkid_column: 'lk id', project_column: 'Проект' };
  const clientMapping = { sheet_name: 'Sheet1', date_column: 'Дата', phone_column: 'Телефон', lkid_column: 'LKID', status_column: 'Статус' };
  const analyzeMapping = { sheet_name: 'Сопоставленные', date_column: 'Дата из ЛК', phone_column: 'Телефон', source_column: 'Полный источник из ЛК', status_column: 'Статус клиента' };
  const sheet = (name: string, columns: string[]) => ({ name, columns, rows: [Object.fromEntries(columns.map(column => [column, column === 'Статус клиента' ? 'Новый статус' : 'synthetic']))] });
  const preview = { filename: 'synthetic.xlsx', sheets: [sheet('Сопоставленные', ['Дата из ЛК', 'Телефон', 'Полный источник из ЛК', 'Статус клиента'])] };
  const job = (id: number, kind: 'match' | 'analyze', status = 'completed') => ({ id, run_id: 'synthetic-run', kind, status, phase: 'Готово', processed_rows: 1, total_rows: 1, error_text: null, output_file_name: 'synthetic.xlsx', export_id: kind === 'analyze' && status === 'completed' ? 5 : null });
  await page.route(/http:\/\/(localhost|127\.0\.0\.1):8000\//, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith('/admin/analytics')) analyticsRequests.push(path);
    let result: unknown = { items: [], total: 0 };
    if (path === '/me') result = { id: 2, name: 'Тестовый клиент', role, login: 'synthetic' };
    else if (path === '/admin/users') result = [];
    else if (path === '/admin/analytics/clients') result = [{ id: 2, name: 'Тестовый клиент' }];
    else if (path.endsWith('/projects') && path.startsWith('/admin/analytics')) result = [
      { id: 101, name: 'Обычный проект', status: 'Активен', collection_source: 'Сайты', deleted_at: null },
      { id: 102, name: 'Исторический Pixel', status: 'Удалён', collection_source: 'Pixel', deleted_at: '2026-01-09' },
      { id: 103, name: 'Новый проект', status: 'Активен', collection_source: 'Звонки', deleted_at: null },
    ];
    else if (path.endsWith('/groups') && path.startsWith('/admin/analytics')) result = [group];
    else if (path.endsWith('/exports') && path.startsWith('/admin/analytics')) result = workflow ? (analysisFinished ? [report, { ...report, id: 6, export_number: 2 }] : []) : [report];
    else if (path.endsWith('/status-rules')) result = { project_rules: [], system_rules: [], status_groups: ['Качественные', 'Недозвон'] };
    else if (path.endsWith('/exports/5/download')) {
      expect(route.request().headers().authorization).toBe(`Bearer ${token}`);
      expect(route.request().url()).not.toContain('token=');
      await route.fulfill({ contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: 'synthetic-file' });
      return;
    } else if (path.endsWith('/groups/11') && route.request().method() === 'DELETE') result = { archived: true };
    else if (path.endsWith('/runs')) result = {
      run_id: 'synthetic-run', project: group.name, lk_row_count: 1,
      lk: { filename: 'Идентификации ЛК.xlsx', detected: lkMapping, sheets: [sheet('leads', ['Дата', 'Телефон', 'Источники', 'lk id', 'Проект'])] },
      client: { filename: 'client.xlsx', detected: clientMapping, sheets: [sheet('Sheet1', ['Дата', 'Телефон', 'LKID', 'Статус'])] },
    };
    else if (path.endsWith('/match/jobs')) result = job(1, 'match', 'queued');
    else if (path.endsWith('/analyze/setup')) result = { ...preview, mapping: analyzeMapping, unknown_statuses: ['Новый статус'], unknown_status_counts: { 'Новый статус': 1 }, status_groups: ['Качественные', 'Недозвон'] };
    else if (path.endsWith('/analyze/jobs')) {
      expect(route.request().postDataJSON().status_rules).toEqual({ 'Новый статус': 'Качественные' });
      expect(route.request().postDataJSON().periods).toEqual([{ period_start: '2026-01-01', period_end: '2026-01-07' }]);
      analysisFinished = true;
      result = job(2, 'analyze', 'queued');
    } else if (path.endsWith('/jobs/1')) result = job(1, 'match');
    else if (path.endsWith('/jobs/2')) result = job(2, 'analyze');
    else if (/\/jobs\/[12]\/preview$/.test(path)) result = preview;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  return analyticsRequests;
}

test('analytics loads on navigation and its styles stay inside the section', async ({ page }) => {
  const requests = await preparePage(page);
  const modules: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/components/leadAnalytics/') || request.url().includes('/components/AdminAnalytics')) modules.push(request.url());
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Аналитика', exact: true })).toBeVisible();
  await expect(page.getByText('Раздел «Обучение» в разработке.')).toBeVisible();
  expect(requests).toEqual([]);
  expect(modules).toEqual([]);
  const before = await page.locator('body').evaluate(element => {
    const css = getComputedStyle(element);
    return [css.fontFamily, css.backgroundColor, css.color];
  });
  await page.getByRole('button', { name: 'Аналитика', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Клиент', exact: true })).toBeVisible();
  await expect.poll(() => requests.includes('/admin/analytics/clients')).toBeTruthy();
  expect(modules.length).toBeGreaterThan(0);
  const after = await page.locator('body').evaluate(element => {
    const css = getComputedStyle(element);
    return [css.fontFamily, css.backgroundColor, css.color];
  });
  expect(after).toEqual(before);
  await page.screenshot({ path: 'test-results/analytics-desktop.png', fullPage: true });
});

test('prepare, match, manually assign unknown status and create final analytics', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics', true);
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  await expect(page.getByLabel('Название группы')).toHaveValue(group.name);
  await page.getByLabel('От *', { exact: true }).fill('2026-01-01');
  await page.getByLabel('До *', { exact: true }).fill('2026-01-07');
  await page.locator('input[type=file]').setInputFiles({ name: 'client.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('synthetic mock input') });
  await page.getByRole('button', { name: 'Подготовить данные', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Проверка колонок' })).toBeVisible();
  await page.getByRole('button', { name: 'Сопоставить', exact: true }).click();
  await page.getByRole('button', { name: 'Сделать аналитику', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Проверьте статусы перед аналитикой' });
  await expect(dialog.getByRole('button', { name: 'Запустить аналитику' })).toBeDisabled();
  await dialog.getByRole('combobox').selectOption('Качественные');
  await dialog.getByRole('button', { name: 'Запустить аналитику' }).click();
  await expect(page.getByRole('heading', { name: 'Аналитика готова' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Скачать аналитику', exact: true })).toBeEnabled();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать аналитику', exact: true }).click();
  await download;
});

test('saved projects and archived group keep downloadable history', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics');
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  await expect(page.getByLabel('Название группы')).toHaveValue(group.name);
  await expect(page.getByRole('checkbox', { name: /Исторический Pixel/ })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: /Новый проект/ })).not.toBeChecked();
  await expect(page.getByText('synthetic.xlsx')).toBeVisible();
  await expect(page.getByRole('link', { name: /сводк|сравнить|сопоставлен/i })).toHaveCount(0);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Excel', exact: true }).click();
  await download;
  await page.getByRole('button', { name: 'Расформировать группу', exact: true }).click();
  await page.getByRole('button', { name: 'Расформировать', exact: true }).click();
  await expect(page.getByText(/Группа расформирована/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Excel', exact: true })).toBeEnabled();
});

for (const role of ['agent', 'client'] as const) {
  test(`${role} cannot open saved analytics section`, async ({ page }) => {
    const requests = await preparePage(page, role, 'analytics');
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Выйти' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Аналитика', exact: true })).toHaveCount(0);
    await expect(page.locator('.lead-analytics')).toHaveCount(0);
    expect(requests).toEqual([]);
  });
}
