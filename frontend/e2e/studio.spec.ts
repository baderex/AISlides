import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';

test('clarify, reload, generate, edit, save and export an editable PowerPoint', async ({ page }, info) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Bring your ideas into focus.' })).toBeVisible();
  await page.getByLabel('AI model provider').selectOption('demo');
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

test('dashboard filters the skill gallery and opens a reusable skill', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('searchbox', { name: 'Search skills' }).fill('academic');
  await expect(page.getByRole('heading', { name: 'Research Brief' })).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'Startup Pitch' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Preview Research Brief' }).click();
  const preview = page.getByRole('dialog', { name: 'Research Brief' });
  await expect(preview).toBeVisible();
  await preview.getByRole('button', { name: 'Use skill', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Remove skill' })).toBeVisible();
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
  await page.getByLabel('AI model provider').selectOption('demo');
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
