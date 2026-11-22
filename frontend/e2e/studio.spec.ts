import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';

test('clarify, reload, generate, edit, save and export an editable PowerPoint', async ({ page }, info) => {
  await page.route('**/api/v1/capabilities', route => route.fulfill({ json: { research: false, images: false } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Bring your ideas into focus.' })).toBeVisible();
  await expect(page.getByLabel('AI model provider')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /settings/i })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^(Professional|Creative|Guide mode)$/ })).toHaveCount(0);
  await page.getByLabel('Describe your presentation').fill('Generate three slides about quantum computing.');
  await page.getByRole('button', { name: 'Generate slides' }).click();
  await expect(page.getByRole('heading', { name: 'Who is this presentation for?' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Who is this presentation for?' })).toBeVisible();
  const plan = page.getByRole('region', { name: 'Generation plan' });
  await expect(plan.locator('s').getByText('Analyze the request')).toHaveCSS('text-decoration-line', 'line-through');
  await expect(plan.locator('[aria-current="step"]')).toContainText('Clarify requirements');
  await expect(plan.getByText('Waiting for you')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Who is this presentation for?' })).toBeInViewport();
  await page.screenshot({ path: info.outputPath('clarification.png') });
  await page.getByRole('radio', { name: /Executive \/ Board/ }).check();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page.getByRole('radio', { name: /Editorial report/ }).check();
  await page.getByRole('button', { name: 'Submit answers' }).click();
  await expect(page.getByRole('button', { name: /Slide 1: Quantum Computing/ })).toBeVisible();
  const slideReady = page.getByText('Slide 1 is ready.', { exact: true });
  const slideActivity = page.locator('details.agent-thinking').filter({ has: slideReady });
  await expect(slideActivity).toBeVisible();
  await expect(slideReady).toBeHidden();
  await expect(page.getByText('Slide outline ready', { exact: true })).toBeVisible();
  await expect(page.getByText('The presentation is ready to edit and export.', { exact: true })).toBeVisible();
  await slideActivity.locator('summary').click();
  await expect(slideReady).toBeVisible();
  await slideActivity.locator('summary').click();
  await expect(slideReady).toBeHidden();
  await expect(plan.locator('s')).toHaveCount(8);
  await expect(plan.locator('s').getByText('Slide 1 · Quantum Computing')).toBeVisible();
  await expect(plan.getByText('8 of 8 tasks complete')).toBeVisible();
  await plan.getByRole('button', { name: /Generation plan/ }).click();
  await expect(plan.locator('ol')).toHaveCount(0);
  await plan.getByRole('button', { name: /Generation plan/ }).click();
  await expect(plan.locator('s')).toHaveCount(8);
  await expect(page.locator('.thumbnail')).toHaveCount(3);
  await expect(page.locator('.canvas-frame')).toBeInViewport({ ratio: .9 });
  await page.screenshot({ path: info.outputPath('workspace.png') });

  await page.getByRole('button', { name: /Slide 2: What makes it different/ }).click();
  const icons = page.locator('.canvas-frame img[src*="/assets/lucide-"]');
  await expect(icons).toHaveCount(2);
  await expect.poll(() => icons.evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0)))
    .toBe(true);
  await page.screenshot({ path: info.outputPath('workspace-icons.png') });
  await page.getByRole('button', { name: /Slide 1: Quantum Computing/ }).click();

  const text = page.locator('.canvas-frame .slide-text').filter({ hasText: /^Quantum Computing$/ });
  await text.click();
  await page.getByRole('button', { name: 'Edit text', exact: true }).click();
  await page.getByRole('dialog', { name: 'Edit slide text' }).getByRole('textbox', { name: 'Text' }).fill('Quantum Computing for Directors');
  await page.getByRole('button', { name: 'Apply text' }).click();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Presentation saved.', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator('.canvas-frame .slide-text').filter({ hasText: /^Quantum Computing for Directors$/ })).toBeVisible();

  await page.getByRole('button', { name: 'Export', exact: true }).click();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: /^PowerPoint Editable/ }).click();
  const download = await downloadPromise;
  const output = info.outputPath('verified.pptx');
  await download.saveAs(output);
  const python = resolve('../backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  execFileSync(python, ['-c', [
    'from pptx import Presentation',
    'from pptx.enum.shapes import MSO_SHAPE_TYPE',
    'import sys',
    'deck = Presentation(sys.argv[1])',
    'assert len(deck.slides) == 3',
    'assert any(s.shape_type == MSO_SHAPE_TYPE.TEXT_BOX and s.text == "Quantum Computing for Directors" for s in deck.slides[0].shapes)',
    'assert any(s.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE for s in deck.slides[0].shapes)',
  ].join('\n'), output]);
});

test('Web research defaults to checked when available and can be turned off', async ({ page }) => {
  let researchAvailable = true;
  await page.route('**/api/v1/capabilities', route => route.fulfill({ json: { research: researchAvailable, images: false } }));
  await page.route('**/api/v1/presentations', route => route.request().method() === 'POST'
    ? route.fulfill({ status: 400, json: { detail: 'Test submission' } })
    : route.continue());
  await page.goto('/');
  const research = page.getByRole('checkbox', { name: 'Web research' });
  const generate = page.getByRole('button', { name: 'Generate slides' });
  const submit = async () => {
    const [request] = await Promise.all([
      page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/api/v1/presentations')),
      generate.click(),
    ]);
    return request.postDataJSON().research;
  };

  await expect(research).toBeChecked();
  await page.getByLabel('Describe your presentation').fill('Search for sources');
  expect(await submit()).toBe(true);
  await research.uncheck();
  expect(await submit()).toBe(false);

  researchAvailable = false;
  const capabilitiesPromise = page.waitForResponse('**/api/v1/capabilities');
  await page.reload();
  await capabilitiesPromise;
  await expect(research).toHaveCount(0);
  await page.getByLabel('Describe your presentation').fill('Create a presentation');
  expect(await submit()).toBe(false);
});

test('submitting while capabilities load preserves automatic research', async ({ page }) => {
  let releaseCapabilities!: () => void;
  const capabilities = new Promise<void>(resolve => { releaseCapabilities = resolve; });
  await page.route('**/api/v1/capabilities', async route => {
    await capabilities;
    await route.fulfill({ json: { research: true, images: false } });
  });
  await page.route('**/api/v1/presentations', route => route.request().method() === 'POST'
    ? route.fulfill({ status: 400, json: { detail: 'Test submission' } })
    : route.continue());
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Describe your presentation').fill('Chart annual electricity generation');
  const [request] = await Promise.all([
    page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/api/v1/presentations')),
    page.getByRole('button', { name: 'Generate slides' }).click(),
  ]);
  releaseCapabilities();
  expect(request.postDataJSON().research).toBeNull();
});

test('the Skills page opens a reusable skill without losing the draft', async ({ page }) => {
  await page.goto('/');
  const prompt = page.getByLabel('Describe your presentation');
  await prompt.fill('A draft about research methods');
  const skills = page.getByRole('navigation', { name: 'Workspace' }).getByRole('link', { name: 'Skill library' });
  await skills.click();
  await expect(page).toHaveURL(/\/skills$/);
  await expect(skills).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { level: 1, name: 'Skill library' })).toBeVisible();
  await expect(prompt).toBeHidden();
  await page.getByRole('searchbox', { name: 'Search skills' }).fill('academic');
  await expect(page.getByRole('heading', { name: 'Research Brief' })).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'Startup Pitch' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Preview Research Brief' }).click();
  const preview = page.getByRole('dialog', { name: 'Research Brief' });
  await expect(preview).toBeVisible();
  await preview.getByRole('button', { name: 'Use skill', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('button', { name: 'Remove skill' })).toBeVisible();
  await expect(prompt).toHaveValue('A draft about research methods');
  await expect(prompt).toBeFocused();
  await page.goto('/skills');
  await expect(page.getByRole('heading', { level: 1, name: 'Skill library' })).toBeVisible();
});

test('the canvas fits saved text and table cells at different viewport widths', async ({ page, request }) => {
  const headline = 'Water Scarcity and Agricultural Importance Define Investment Opportunity Across Middle Eastern Countries';
  const created = await request.post('/api/v1/presentations', { data: { prompt: 'Text fit check', generate: false } });
  expect(created.status()).toBe(201);
  const { id, revision, document } = await created.json();
  const saved = await request.patch('/api/v1/presentations/' + id, { data: {
    revision,
    document: { ...document, slides: [{ title: headline, layout: 'comparison', elements: [
      { type: 'text', x: .06, y: .08, w: .70, h: .15, text: headline, size: 36, bold: true },
      { type: 'table', x: .06, y: .3, w: .60, h: .22, size: 24, rows: [
        ['Topic', 'Explain the expected outcome in plain language.'],
        ['Timing', 'Review the result after answering the practice question.'],
      ] },
    ] }] },
  } });
  expect(saved.status()).toBe(200);
  await page.goto('/presentation/' + id);
  const box = page.locator('.canvas-frame .slide-text').filter({ hasText: headline });
  const table = page.locator('.canvas-frame .slide-content > table');
  await expect(box).toBeVisible();
  await expect(table).toBeVisible();

  for (const width of [1440, 900]) {
    await page.setViewportSize({ width, height: 960 });
    for (const [element, requested] of [[box, 36], [table, 24]] as const) {
      await expect.poll(() => element.evaluate((node, size) => {
        const frame = node.parentElement!;
        const nominal = node.closest('.slide-canvas')!.clientWidth * size / 960;
        return node.scrollHeight <= frame.clientHeight && node.scrollWidth <= frame.clientWidth
          && parseFloat(getComputedStyle(node).fontSize) < nominal - .1;
      }, requested)).toBe(true);
    }
  }
});

test('Recent Presentations supports confirmed deletion and links to the full list', async ({ page, request }) => {
  const title = 'Presentation to delete';
  const created = await request.post('/api/v1/presentations', { data: { prompt: title, generate: false } });
  expect(created.status()).toBe(201);
  const { id } = await created.json();

  await page.goto('/');
  const recent = page.getByRole('region', { name: 'Recent presentations' });
  const deleteButton = recent.getByRole('button', { name: `Delete ${title}` });
  await deleteButton.click();
  const confirm = page.getByRole('dialog', { name: 'Delete presentation?' });
  await expect(confirm.getByText(title)).toBeVisible();
  await confirm.getByRole('button', { name: 'Cancel' }).click();
  await expect(deleteButton).toBeVisible();

  await recent.getByRole('link', { name: 'View all presentations' }).click();
  await expect(page).toHaveURL(/\/presentations$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Presentations' })).toBeVisible();
  await expect(page.getByRole('button', { name: `Delete ${title}` })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Skill library' })).toHaveCount(0);
  await page.goBack();
  await deleteButton.click();
  await confirm.getByRole('button', { name: 'Delete presentation' }).click();
  await expect(recent.getByRole('status')).toHaveText('Presentation deleted.');
  await expect(deleteButton).toHaveCount(0);
  expect((await request.get(`/api/v1/presentations/${id}`)).status()).toBe(404);

  await page.goto('/presentations');
  await expect(page.getByRole('heading', { level: 1, name: 'Presentations' })).toBeVisible();
  await expect(page.getByRole('button', { name: `Delete ${title}` })).toHaveCount(0);
});

test('the Skill library confirms deletion and clears a deleted draft skill', async ({ page }) => {
  const name = 'Skill to delete';
  await page.goto('/skills');
  await expect(page.getByRole('button', { name: 'Use Research Brief' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Delete Research Brief' })).toHaveCount(0);
  await page.getByRole('button', { name: 'New skill' }).click();
  const create = page.getByRole('dialog', { name: 'New skill' });
  await create.getByLabel('Name', { exact: true }).fill(name);
  await create.getByLabel('Description').fill('A test skill for the library.');
  await create.getByRole('button', { name: 'Create skill' }).click();
  await expect(page.getByRole('button', { name: 'Remove skill' })).toBeVisible();

  await page.getByRole('navigation', { name: 'Workspace' }).getByRole('link', { name: 'Skill library' }).click();
  const remove = page.getByRole('button', { name: `Delete ${name}` });
  await remove.click();
  const confirm = page.getByRole('dialog', { name: 'Delete skill?' });
  await expect(confirm.getByText(name)).toBeVisible();
  await confirm.getByRole('button', { name: 'Cancel' }).click();
  await expect(remove).toBeVisible();
  await remove.click();
  await confirm.getByRole('button', { name: 'Delete skill' }).click();
  await expect(page.getByRole('status')).toHaveText('Skill deleted.');
  await expect(remove).toHaveCount(0);
  await page.getByRole('link', { name: 'Home', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Remove skill' })).toHaveCount(0);

  await page.goto('/skills');
  await expect(page.getByRole('button', { name: 'Use Research Brief' })).toBeVisible();
  await expect(page.getByRole('heading', { name })).toHaveCount(0);
});

test('import a branded template and review the generated PowerPoint', async ({ page }, info) => {
  test.skip(!process.env['POWERPOINT_RENDERER'], 'Set POWERPOINT_RENDERER to run the Impress integration.');
  test.setTimeout(85000);
  const python = resolve('../backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const template = info.outputPath('Company.pptx');
  execFileSync(python, ['-c', [
    'from pptx import Presentation',
    'import sys',
    'deck = Presentation()',
    'deck.slide_layouts[0].name = "Company Cover"',
    'deck.save(sys.argv[1])',
  ].join('\n'), template]);

  await page.goto('/');
  await page.getByRole('button', { name: 'New skill', exact: true }).click();
  await page.getByRole('button', { name: 'Branded PPTX' }).click();
  await page.getByLabel('PowerPoint template').setInputFiles(template);
  await page.getByRole('button', { name: 'Create skill' }).click();
  await expect(page.getByRole('dialog', { name: 'New skill' })).toBeHidden({ timeout: 20000 });
  await expect(page.getByRole('button', { name: 'Remove skill' })).toBeVisible();
  await expect(page.getByLabel('Slide aspect ratio')).toHaveValue('4:3');
  await page.getByLabel('Describe your presentation').fill('Generate three slides about quantum computing for executives.');
  await page.getByRole('button', { name: 'Generate slides' }).click();
  await expect(page.getByText('The presentation is ready to edit and export.', { exact: true })).toBeVisible({ timeout: 15000 });

  await page.getByRole('button', { name: 'Export', exact: true }).click();
  await page.getByRole('button', { name: 'Review PowerPoint export' }).click();
  const review = page.getByRole('dialog', { name: 'PowerPoint export review' });
  await expect(review).toBeVisible({ timeout: 55000 });
  await review.getByRole('combobox', { name: 'Slide', exact: true }).selectOption('0', { timeout: 5000 });
  await expect(review.getByRole('img', { name: /Rendered slide 1/ })).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await review.getByRole('link', { name: 'Download reviewed PowerPoint' }).click();
  const download = await downloadPromise;
  const deck = info.outputPath('reviewed.pptx');
  await download.saveAs(deck);
  execFileSync(python, ['-c', [
    'from pptx import Presentation',
    'import sys',
    'deck = Presentation(sys.argv[1])',
    'assert len(deck.slides) == 3',
    'assert deck.slides[0].slide_layout.name == "Company Cover"',
    'assert deck.slides[0].shapes.title.text == "Quantum Computing"',
  ].join('\n'), deck]);
});
