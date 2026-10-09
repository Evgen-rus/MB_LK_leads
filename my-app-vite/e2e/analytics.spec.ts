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
  const sheet = (name: string, columns: string[]) => ({ name, columns, rows: Array.from({ length: 8 }, () => Object.fromEntries(columns.map(column => [column, column === 'Статус клиента' ? 'Новый статус' : 'synthetic']))) });
  const preview = { filename: 'synthetic.xlsx', sheets: [sheet('Сопоставленные', ['Дата из ЛК', 'Телефон', 'Полный источник из ЛК', 'Статус клиента'])] };
  const savedResult = {
    id: 5,
    period_start: '2026-01-01',
    period_end: '2026-01-07',
    periods: [{
      id: 'saved-overall', period_start: '2026-01-01', period_end: '2026-01-07',
      metrics: { 'Период': '01.01.2026–07.01.2026', 'Всего идентификаций': 10, 'Качественные': 4, 'Кач. %': 0.4,
        'Рабочий потенциал': 3, 'Рабочий потенциал %': 0.3, 'Сигнал спроса': 2, 'Сигнал спроса %': 0.2,
        'Недозвон': 2, 'Недозвон %': 0.2, _fills: { 'Кач. %': 'C6EFCE' } },
    }],
    breakdowns: { 'saved-overall': { domain_channel: [], source_channel: [], channel: [] } },
  };
  const resultRows = [
    { 'Полный источник': 'synthetic alpha', 'Канал': 'Поиск', 'Количество': 12, 'Кач. %': 0.5 },
    { 'Полный источник': 'synthetic beta', 'Канал': 'Рекомендация', 'Количество': 8, 'Кач. %': 0.25 },
  ];
  const job = (id: number, kind: 'match' | 'analyze', status = 'completed') => ({ id, run_id: 'synthetic-run', kind, status, phase: 'Готово', processed_rows: 1, total_rows: 1, error_text: null, output_file_name: 'synthetic.xlsx', export_id: kind === 'analyze' && status === 'completed' ? 5 : null });
  await page.route(/http:\/\/(localhost|127\.0\.0\.1):8000\//, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith('/admin/analytics')) analyticsRequests.push(path + new URL(route.request().url()).search);
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
    else if (path.endsWith('/exports/5/result/rows')) {
      const url = new URL(route.request().url());
      const table = url.searchParams.get('table') ?? 'data';
      const columns = table === 'statuses' ? ['Группа статуса', 'Исходный статус', 'Количество'] : ['Дата', 'Полный источник', 'Канал', 'Количество', 'Кач. %'];
      const rows = table === 'statuses' ? [{ 'Группа статуса': 'Качественные', 'Исходный статус': 'synthetic status', 'Количество': 4 }] : resultRows;
      result = { columns, rows, total: 250, page: Number(url.searchParams.get('page') ?? '1'), page_size: 100, available: true,
        values: { 'Полный источник': ['', 'synthetic alpha', 'synthetic beta'], 'Канал': ['Поиск', 'Рекомендация'] } };
    }
    else if (path.endsWith('/exports/5/result')) result = savedResult;
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
      expect(route.request().postDataJSON().periods).toEqual([
        { period_start: '2026-01-01', period_end: '2026-01-07' },
        { period_start: '2026-01-01', period_end: '2026-01-04' },
        { period_start: '2026-01-05', period_end: '2026-01-07' },
      ]);
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
  await expect(page.getByRole('combobox', { name: 'Группа', exact: true })).toHaveValue('11');
  await expect(page.getByText('Сохранить месяцы и недели для выбора в отчёте', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Разбить по|Добавить период/ })).toHaveCount(0);
  await page.getByLabel('От *', { exact: true }).fill('2026-01-01');
  await page.getByLabel('До *', { exact: true }).fill('2026-01-07');
  await page.locator('input[type=file]').setInputFiles({ name: 'client.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('synthetic mock input') });
  await page.getByRole('button', { name: 'Подготовить данные', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Проверка колонок' })).toBeVisible();
  await expect(page.locator('.mappingPanels').getByText(/^Пример:/)).toHaveCount(0);
  for (const panel of await page.locator('.mappingPanels > .mappingPanel').all()) {
    await expect(panel.locator('tbody tr')).toHaveCount(5);
  }
  const panelGeometry = await page.locator('.mappingPanels > .mappingPanel').evaluateAll(panels => panels.map(panel => ({
    tableTop: panel.querySelector('.tableWrap')!.getBoundingClientRect().top,
    selectHeights: [...panel.querySelectorAll('select')].map(select => select.getBoundingClientRect().height),
  })));
  expect(Math.abs(panelGeometry[0].tableTop - panelGeometry[1].tableTop)).toBeLessThanOrEqual(1);
  expect(new Set(panelGeometry.flatMap(panel => panel.selectHeights)).size).toBe(1);
  await page.screenshot({ path: 'test-results/analytics-mapping-desktop.png', fullPage: true });
  const desktopViewport = page.viewportSize()!;
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator('.mappingPanels > .mappingPanel').evaluateAll(panels => panels.every(panel => panel.getBoundingClientRect().right <= window.innerWidth))).toBeTruthy();
  await page.screenshot({ path: 'test-results/analytics-mapping-mobile.png', fullPage: true });
  await page.setViewportSize(desktopViewport);
  await page.getByRole('button', { name: 'Сопоставить', exact: true }).click();
  const periodLists = page.locator('.periodDisclosure');
  await expect(periodLists).toHaveCount(2);
  for (const list of await periodLists.all()) {
    await expect(list).not.toHaveAttribute('open', '');
    await list.locator('summary').click();
    await expect(list).toHaveAttribute('open', '');
    await expect(list.getByText('2026-01-05 — 2026-01-07', { exact: true })).toBeVisible();
    await list.locator('summary').click();
    await expect(list.getByText('2026-01-05 — 2026-01-07', { exact: true })).toBeHidden();
  }
  await page.screenshot({ path: 'test-results/analytics-periods-setup.png', fullPage: true });
  await page.getByRole('button', { name: 'Сделать аналитику', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Проверьте статусы перед аналитикой' });
  await expect(dialog.getByRole('button', { name: 'Запустить аналитику' })).toBeDisabled();
  await dialog.getByRole('combobox').selectOption('Качественные');
  await dialog.getByRole('button', { name: 'Запустить аналитику' }).click();
  await expect(page.getByRole('heading', { name: 'Тестовая группа', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Скачать Excel', exact: true })).toBeEnabled();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать Excel', exact: true }).click();
  await download;
  const nav = page.getByRole('navigation', { name: 'Шаги обработки' });
  await nav.getByRole('button', { name: /Аналитика/ }).click();
  const analysisDate = page.getByLabel('Дата анализа', { exact: true });
  const previousDate = await analysisDate.inputValue();
  page.once('dialog', dialog => dialog.dismiss());
  await analysisDate.fill('2026-01-09');
  await expect(analysisDate).toHaveValue(previousDate);
  await expect(nav.getByRole('button', { name: /Результат/ })).toBeEnabled();
  page.once('dialog', dialog => dialog.accept());
  await analysisDate.fill('2026-01-09');
  await expect(analysisDate).toHaveValue('2026-01-09');
  await expect(page.getByRole('button', { name: 'Сделать аналитику', exact: true })).toBeEnabled();
  await expect(nav.getByRole('button', { name: /Результат/ })).toBeDisabled();
  await expect(nav.getByRole('button', { name: /Колонки/ })).toBeEnabled();
});

test('saved projects and archived group keep downloadable history', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics');
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  await expect(page.getByRole('combobox', { name: 'Группа', exact: true })).toHaveValue('11');
  await page.getByRole('button', { name: 'Редактировать группу', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: /Исторический Pixel/ })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: /Новый проект/ })).not.toBeChecked();
  await page.getByRole('button', { name: 'Отмена', exact: true }).click();
  await page.getByRole('navigation', { name: 'Шаги обработки' }).getByRole('button', { name: /История/ }).click();
  await expect(page.getByText('synthetic.xlsx')).toBeVisible();
  await expect(page.getByRole('link', { name: /сводк|сравнить|сопоставлен/i })).toHaveCount(0);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Excel', exact: true }).click();
  await download;
  await page.getByRole('button', { name: 'Редактировать группу', exact: true }).click();
  await page.getByRole('button', { name: 'Расформировать группу', exact: true }).click();
  await page.getByRole('button', { name: 'Расформировать', exact: true }).click();
  await expect(page.getByText(/Группа расформирована/)).toBeVisible();
  await page.getByRole('navigation', { name: 'Шаги обработки' }).getByRole('button', { name: /История/ }).click();
  await expect(page.getByRole('button', { name: 'Excel', exact: true })).toBeEnabled();
});

test('history opens saved native results, marks unavailable Excel, and queries the archived sheet', async ({ page }) => {
  const requests = await preparePage(page, 'admin', 'analytics');
  await page.route('**/admin/analytics/groups/11/exports', async route => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify([{ ...report, report_available: false }]) });
  });
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  await page.getByRole('navigation', { name: 'Шаги обработки' }).getByRole('button', { name: /История/ }).click();
  await expect(page.getByRole('heading', { name: 'История аналитики' })).toBeVisible();
  await page.getByRole('button', { name: 'Открыть аналитику', exact: true }).click();
  await expect(page.getByLabel('Другие сохранённые периоды')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Тестовая группа', exact: true })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Шаги обработки' })).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: 'Клиент', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Скачать Excel', exact: true })).toBeDisabled();
  await expect(page.getByRole('region', { name: 'Ключевые показатели' }).getByText('10', { exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Итог' })).toHaveAttribute('aria-selected', 'true');

  await page.getByRole('tab', { name: 'Статусы' }).click();
  await expect(page.getByText('Общий срез.')).toBeVisible();
  await expect(page.getByText('synthetic status')).toBeVisible();
  await page.getByRole('tab', { name: 'Данные' }).click();
  await expect(page.getByText('synthetic alpha')).toBeVisible();
  await page.getByPlaceholder('Поиск по таблице...').fill('synthetic alpha');
  await expect.poll(() => requests.some(value => value.includes('/result/rows?') && value.includes('query=synthetic'))).toBeTruthy();

  await page.getByRole('button', { name: 'Фильтр: Полный источник' }).click();
  const filter = page.getByRole('dialog', { name: 'Фильтр: Полный источник' });
  await filter.getByRole('checkbox', { name: 'synthetic alpha' }).check();
  await filter.getByRole('button', { name: 'Применить' }).click();
  await expect.poll(() => requests.some(value => value.includes('/result/rows?') && value.includes('filters='))).toBeTruthy();
  const serializedFilters = requests.map(value => {
    const query = value.split('?')[1];
    return query ? new URLSearchParams(query).get('filters') : null;
  }).find(Boolean);
  expect(JSON.parse(serializedFilters!)['Полный источник'].selected).toEqual(['synthetic alpha']);

  await page.getByRole('button', { name: 'Следующая страница' }).click();
  await expect.poll(() => requests.some(value => value.includes('/result/rows?') && value.includes('page=2'))).toBeTruthy();
  await expect(page.getByRole('button', { name: 'К истории', exact: true })).toBeVisible();
});

test('history collapses additional periods and their metrics together', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics');
  await page.route('**/admin/analytics/groups/11/exports', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify([{ ...report, periods: [period,
      { ...period, period_start: '2026-01-01', period_end: '2026-01-04', total_count: 7 },
      { ...period, period_start: '2026-01-05', period_end: '2026-01-07', total_count: 3 },
    ] }]),
  }));
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  await page.getByRole('navigation', { name: 'Шаги обработки' }).getByRole('button', { name: /История/ }).click();
  const row = page.locator('.historyWrap tbody tr');
  const list = row.locator('.periodDisclosure');
  await expect(row.locator('td').nth(4).locator('div')).toHaveCount(1);
  await expect(list.getByText('2026-01-05 — 2026-01-07', { exact: true })).toBeHidden();
  await list.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(list.getByText('2026-01-05 — 2026-01-07', { exact: true })).toBeVisible();
  await expect(row.locator('td').nth(4).locator('div')).toHaveCount(3);
  await list.locator('summary').click();
  await expect(row.locator('td').nth(4).locator('div')).toHaveCount(1);
  await page.screenshot({ path: 'test-results/analytics-periods-history.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await list.locator('summary').click();
  await expect(row.locator('td').nth(4).locator('div')).toHaveCount(3);
  await page.screenshot({ path: 'test-results/analytics-periods-history-mobile.png', fullPage: true });
});

test('selected weeks combine across months and apply to every result table', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics');
  const requests: string[][] = [];
  const metrics = { 'Всего идентификаций': 10, 'Качественные': 4, 'Кач. %': 0.4 };
  const periods = [
    { id: 'overall', period_start: '2026-09-01', period_end: '2026-10-07', metrics },
    { id: 'september', period_start: '2026-09-01', period_end: '2026-09-30', metrics },
    { id: 'october', period_start: '2026-10-01', period_end: '2026-10-07', metrics },
    { id: 'sep-week', period_start: '2026-09-14', period_end: '2026-09-20', metrics },
    { id: 'oct-week', period_start: '2026-10-01', period_end: '2026-10-04', metrics },
  ];
  await page.route(/\/exports\/5\/result(?:\?|$)/, async route => {
    const ids = new URL(route.request().url()).searchParams.getAll('period_ids');
    requests.push(ids);
    const selectedMetrics = { ...metrics, 'Всего идентификаций': ids.length * 10 };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      id: 5, period_start: '2026-09-01', period_end: '2026-10-07',
      periods: ids.length ? [{ id: 'selection', period_start: '2026-09-14', period_end: '2026-10-04', metrics: selectedMetrics }] : periods,
      breakdowns: { selection: { domain_channel: [{ 'Домен': 'synthetic.example', 'Канал': 'B1', ...selectedMetrics }],
        source_channel: [{ 'Полный источник': 'synthetic.example', 'Канал': 'B1', ...selectedMetrics }],
        channel: [{ 'Канал': 'B1', ...selectedMetrics }] } },
    }) });
  });
  const rowRequests: string[][] = [];
  await page.route(/\/exports\/5\/result\/rows\?/, async route => {
    const url = new URL(route.request().url());
    rowRequests.push(url.searchParams.getAll('period_ids'));
    const columns = url.searchParams.get('table') === 'statuses' ? ['Группа статуса', 'Количество'] : ['Дата', 'Канал'];
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ columns, rows: [], total: 0,
      page: 1, page_size: 100, available: true }) });
  });
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  await page.getByRole('navigation', { name: 'Шаги обработки' }).getByRole('button', { name: /История/ }).click();
  await page.getByRole('button', { name: 'Открыть аналитику', exact: true }).click();
  await page.getByRole('button', { name: 'Несколько недель', exact: true }).click();
  await expect(page.locator('.resultKpi strong').first()).toHaveText('—');
  const september = page.getByRole('button', { name: '14.09–20.09', exact: true });
  await september.click();
  await expect(september).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.resultKpi strong').first()).toHaveText('10');
  await page.getByRole('button', { name: 'Октябрь', exact: true }).click();
  await page.getByRole('button', { name: '01.10–04.10', exact: true }).click();
  await expect(page.locator('.resultKpi strong').first()).toHaveText('20');
  expect(requests.at(-1)).toEqual(['sep-week', 'oct-week']);
  await expect(page.getByRole('button', { name: 'Excel всего отчёта', exact: true })).toBeVisible();
  for (const name of ['Домены', 'Источники', 'Каналы']) {
    await page.getByRole('tab', { name, exact: true }).click();
    await expect(page.locator('.resultTable tbody tr')).toHaveCount(1);
    await expect(page.locator('.resultKpi strong').first()).toHaveText('20');
  }
  await page.getByRole('tab', { name: 'Динамика', exact: true }).click();
  await expect(page.locator('.resultTable tbody tr')).toHaveCount(2);
  for (const name of ['Статусы', 'Данные']) {
    await page.getByRole('tab', { name, exact: true }).click();
    await expect.poll(() => rowRequests.at(-1)).toEqual(['sep-week', 'oct-week']);
    await expect(page.locator('.resultKpi strong').first()).toHaveText('20');
  }
  await page.getByRole('button', { name: 'Сентябрь', exact: true }).click();
  await expect(september).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('tab', { name: 'Итог', exact: true }).click();
  for (const width of [1582, 390]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: `test-results/analytics-selected-weeks-${width}.png`, fullPage: true });
  }
  await september.click();
  await expect(page.locator('.resultKpi strong').first()).toHaveText('10');
  await page.getByRole('button', { name: 'Снять выбор недель', exact: true }).click();
  await expect(page.locator('.resultKpi strong').first()).toHaveText('—');
  await page.getByRole('button', { name: 'Весь период', exact: true }).click();
  await expect(page.locator('.resultKpi strong').first()).toHaveText('10');
  await expect(page.getByRole('button', { name: 'Несколько недель', exact: true })).toHaveAttribute('aria-pressed', 'false');
});

test('large saved result keeps one page scrollbar and contains table scrolling', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics');
  const metrics = { 'Всего идентификаций': 100, 'Качественные': 50, 'Кач. %': 0.5 };
  await page.route('**/admin/analytics/groups/11/exports/5/result', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      id: 5, ...period,
      periods: [{ id: 'overall', period_start: period.period_start, period_end: period.period_end, metrics }],
      breakdowns: { overall: { domain_channel: [], channel: [], source_channel: Array.from({ length: 100 }, (_, index) => ({
        'Полный источник': `synthetic-${index}`, 'Канал': 'Поиск', ...metrics,
      })) } },
    }),
  }));
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  await page.getByRole('navigation', { name: 'Шаги обработки' }).getByRole('button', { name: /История/ }).click();
  await page.getByRole('button', { name: 'Открыть аналитику', exact: true }).click();
  await page.getByRole('tab', { name: 'Источники', exact: true }).click();
  await expect(page.locator('.resultTable tbody tr')).toHaveCount(100);
  for (const viewport of [{ width: 1582, height: 866 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.locator('main').evaluate(element => element.scrollHeight - element.clientHeight)).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => document.documentElement.scrollHeight > innerHeight)).toBeTruthy();
    const table = page.locator('.resultTableShell');
    expect(await table.evaluate(element => element.scrollHeight > element.clientHeight)).toBeTruthy();
    await table.evaluate(element => { element.scrollTop = element.scrollHeight; });
    expect(await table.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    await table.evaluate(element => { element.scrollTop = 0; });
    await page.screenshot({ path: `test-results/analytics-scroll-${viewport.width}.png`, fullPage: true });
  }
});

test('group editing is explicit, cancel restores selection, save closes editor', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics');
  let saved = { ...group };
  let writes = 0;
  await page.route('**/admin/analytics/groups/11', async route => {
    if (route.request().method() !== 'PUT') return route.fallback();
    writes++;
    saved = { ...saved, ...route.request().postDataJSON() };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  const edit = page.getByRole('button', { name: 'Редактировать группу', exact: true });
  await expect(edit).toBeVisible();
  await expect(page.locator('.projectSelectionList input[type="checkbox"]')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'История аналитики' })).toHaveCount(0);
  await edit.click();
  await page.getByRole('checkbox', { name: /Исторический Pixel/ }).uncheck();
  await page.getByRole('button', { name: 'Отмена', exact: true }).click();
  expect(writes).toBe(0);
  await edit.click();
  await expect(page.getByRole('checkbox', { name: /Исторический Pixel/ })).toBeChecked();
  await page.getByRole('checkbox', { name: /Исторический Pixel/ }).uncheck();
  await page.getByRole('button', { name: 'Сохранить изменения', exact: true }).click();
  await expect(edit).toBeVisible();
  await expect(page.locator('.projectSelectionList input[type="checkbox"]')).toHaveCount(0);
  expect(writes).toBe(1);
  expect(saved.project_ids).toEqual([101]);
  await page.screenshot({ path: 'test-results/analytics-protected-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(edit).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: 'test-results/analytics-protected-mobile.png', fullPage: true });
});

test('step navigation and history preserve work, only confirmed changes invalidate later steps', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics', true);
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  const nav = page.getByRole('navigation', { name: 'Шаги обработки' });
  const files = nav.getByRole('button', { name: /Файлы/ });
  const mapping = nav.getByRole('button', { name: /Колонки/ });
  const analysis = nav.getByRole('button', { name: /Аналитика/ });
  const result = nav.getByRole('button', { name: /Результат/ });
  const history = nav.getByRole('button', { name: /История/ });
  await expect(mapping).toBeDisabled();
  await expect(analysis).toBeDisabled();
  await expect(result).toBeDisabled();
  await page.getByLabel('От *', { exact: true }).fill('2026-01-01');
  await page.getByLabel('До *', { exact: true }).fill('2026-01-07');
  await page.locator('input[type=file]').setInputFiles({ name: 'client.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('synthetic') });
  await page.getByRole('button', { name: 'Подготовить данные', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Проверка колонок' })).toBeVisible();
  await history.click();
  await expect(page.getByRole('heading', { name: 'История аналитики' })).toBeVisible();
  await mapping.click();
  await expect(page.getByRole('heading', { name: 'Проверка колонок' })).toBeVisible();
  await page.getByRole('button', { name: 'Редактировать группу', exact: true }).click();
  await page.getByRole('checkbox', { name: /Исторический Pixel/ }).uncheck();
  await page.getByRole('button', { name: 'Отмена', exact: true }).click();
  await expect(mapping).toBeEnabled();
  await page.getByRole('button', { name: 'Сопоставить', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Сделать аналитику', exact: true })).toBeVisible();
  await mapping.click();
  const sourceColumn = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: 'Идентификации ЛК', exact: true }) }).getByRole('combobox', { name: /^Полный источник/ });
  page.once('dialog', dialog => dialog.dismiss());
  await sourceColumn.selectOption('Телефон');
  await expect(sourceColumn).toHaveValue('Источники');
  await expect(analysis).toBeEnabled();
  page.once('dialog', dialog => dialog.accept());
  await sourceColumn.selectOption('Телефон');
  await expect(sourceColumn).toHaveValue('Телефон');
  await expect(analysis).toBeDisabled();
  await expect(mapping).toBeEnabled();
  await sourceColumn.selectOption('Источники');
  await page.getByRole('button', { name: 'Сопоставить', exact: true }).click();
  await expect(analysis).toBeEnabled();
  await files.click();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByLabel('До *', { exact: true }).fill('2026-01-08');
  await expect(page.getByLabel('До *', { exact: true })).toHaveValue('2026-01-07');
  await expect(analysis).toBeEnabled();
  page.once('dialog', dialog => dialog.accept());
  await page.getByLabel('До *', { exact: true }).fill('2026-01-08');
  await expect(page.getByLabel('До *', { exact: true })).toHaveValue('2026-01-08');
  await expect(mapping).toBeDisabled();
  await expect(analysis).toBeDisabled();
  await expect(result).toBeDisabled();
  await history.click();
  await expect(page.getByRole('heading', { name: 'История аналитики' })).toBeVisible();
});

test('context switches confirm draft periods and unnamed group project selections', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics');
  await page.goto('/');
  const clientSelect = page.getByRole('combobox', { name: 'Клиент', exact: true });
  const groupSelect = page.getByRole('combobox', { name: 'Группа', exact: true });
  await clientSelect.selectOption('2');
  await expect(page.getByRole('button', { name: 'Редактировать группу', exact: true })).toBeVisible();
  await page.getByLabel('От *', { exact: true }).fill('2026-01-01');
  page.once('dialog', dialog => dialog.dismiss());
  await groupSelect.selectOption('new');
  await expect(groupSelect).toHaveValue('11');
  await expect(page.getByLabel('От *', { exact: true })).toHaveValue('2026-01-01');
  page.once('dialog', dialog => dialog.accept());
  await groupSelect.selectOption('new');
  await expect(page.getByLabel('Название группы')).toHaveValue('');
  await page.getByRole('checkbox', { name: /Обычный проект/ }).check();
  page.once('dialog', dialog => dialog.dismiss());
  await clientSelect.selectOption('');
  await expect(clientSelect).toHaveValue('2');
  await expect(page.getByRole('checkbox', { name: /Обычный проект/ })).toBeChecked();
  page.once('dialog', dialog => dialog.accept());
  await clientSelect.selectOption('');
  await expect(clientSelect).toHaveValue('');
});

test('client status rules can be resolved, added, edited and deleted across groups', async ({ page }) => {
  await preparePage(page, 'admin', 'analytics');
  const categories = ['Качественные', 'Недозвон'];
  let rules: { id: number; pattern: string; match_type: string; group_name: string; priority: number; source: string }[] = [];
  let conflicts = [{ pattern: 'Спорный статус', group_names: categories }];
  let nextId = 1;
  await page.route('**/admin/analytics/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let result: unknown;
    if (path === '/admin/analytics/clients/2/groups') {
      result = [group, { ...group, id: 12, name: 'Другая группа' }];
    } else if (/\/groups\/(11|12)\/status-rules$/.test(path)) {
      if (method === 'POST') {
        const payload = route.request().postDataJSON();
        const rule = { id: nextId++, pattern: payload.pattern, match_type: 'exact', group_name: payload.group_name, priority: 10, source: 'project' };
        rules.push(rule);
        conflicts = conflicts.filter(item => item.pattern !== rule.pattern);
        result = rule;
      } else {
        result = { project_rules: rules, system_rules: [], status_groups: categories, conflicts };
      }
    } else if (/\/groups\/(11|12)\/status-rules\/\d+$/.test(path)) {
      const id = Number(path.split('/').at(-1));
      if (method === 'DELETE') {
        rules = rules.filter(rule => rule.id !== id);
        result = { deleted: true };
      } else {
        const rule = rules.find(rule => rule.id === id)!;
        rule.group_name = route.request().postDataJSON().group_name;
        result = rule;
      }
    } else {
      await route.fallback();
      return;
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Клиент', exact: true }).selectOption('2');
  await page.getByRole('button', { name: 'Соответствия статусов', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Соответствия статусов клиента', exact: true });
  await dialog.getByLabel('Категория для статуса Спорный статус').selectOption('Качественные');
  await dialog.getByRole('button', { name: 'Сохранить выбор', exact: true }).click();
  await expect(dialog.getByLabel('Категория для статуса Спорный статус')).toHaveCount(0);
  await dialog.getByLabel('Исходный статус', { exact: true }).fill('Новый статус');
  await dialog.locator('form').getByRole('combobox').selectOption('Недозвон');
  await dialog.getByRole('button', { name: 'Добавить правило', exact: true }).click();
  await expect(dialog.getByRole('row').filter({ hasText: 'Новый статус' })).toHaveCount(1);
  await dialog.getByRole('button', { name: 'Закрыть', exact: true }).last().click();
  await page.getByRole('combobox', { name: 'Группа', exact: true }).selectOption('12');
  await page.getByRole('button', { name: 'Соответствия статусов', exact: true }).click();
  const row = dialog.getByRole('row').filter({ hasText: 'Новый статус' });
  await expect(row.getByRole('combobox')).toHaveValue('Недозвон');
  await row.getByRole('combobox').selectOption('Качественные');
  await row.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(row.getByRole('combobox')).toHaveValue('Качественные');
  await row.getByRole('button', { name: 'Удалить', exact: true }).click();
  await row.getByRole('button', { name: 'Да', exact: true }).click();
  await expect(row).toHaveCount(0);
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
