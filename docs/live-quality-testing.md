# Live slide quality testing

This workflow is manually dispatched; no API calls or billing are triggered by pushes or pull requests.

## Configuration

In Settings → Secrets and variables → Actions, add repository secret `LLM_API_KEY` (a dedicated OpenRouter key). Add `RESEARCH_API_KEY` only for the optional Brave research case. Never commit a real .env.

The workflow writes only non-secret settings into backend/.env:
- OpenRouter API URL: https://openrouter.ai/api/v1
- Model: deepseek/deepseek-v4.1-flash
- Vision enabled
- Image generation disabled

Credentials are process environment variables scoped to the live benchmark step. Regression tests use fixture model responses. No image-generation requests are made. The product test supplies an original synthetic image instead.

## Run

After this workflow is present on the default branch, open Actions → Slide quality tests → Run workflow.

- **regression** (default): backend pytest, frontend tests, build, Playwright browser tests, no live model.
- **smoke**: regression plus one four-slide financial benchmark.
- **full**: regression plus the executive, financial, product, and technical benchmark decks (18 slides total).
- Check **web_research** on smoke or full to add a three-slide live Brave research benchmark. Requires the optional secret.

Live test limits are conservative estimates based on the application's token accounting, not a guaranteed OpenRouter bill. Use a dedicated OpenRouter key with its own spend cap. The run is limited to 60 minutes and each generation job is limited to 150,000 tokens and an estimated $3. The workflow never retries an entire paid run automatically.

## Review evidence

Download the regression-results and live-slide-quality-* artifacts (seven-day retention). Each completed live case includes the document JSON, PPTX, app-rendered PDF, slide PNGs, preview contact sheet, LibreOffice-rendered PDF and image contact sheet, and run.json with token usage, timing, warnings, and export findings. Failed cases retain failure.json and any already produced artifacts. The summary.json identifies which cases completed.

Inspect the preview and Office contact sheets independently for visual hierarchy, font size, alignment, chart readability, visual variety, missing required content, and export differences. Passing benchmark fidelity checks is not a claim of professional-level design or correctness of every generated statement. LibreOffice is a proxy for Office rendering, not a Microsoft PowerPoint validation.

The repository may be public; use only the provided fictional/test briefs. Never add confidential prompts or documents to this workflow or its artifacts.
