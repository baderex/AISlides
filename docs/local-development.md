# Local development

Install Git, Python 3.11 or later, and Node 20.19+ (20.x), 22.13+ (22.x), or 24.x with npm. Run `python scripts/setup.py` followed by `python scripts/dev.py` from the repository root. Setup creates `backend/.venv`, copies the example `.env` files where needed, installs dependencies, applies migrations and seeds the skill library. The development supervisor starts the API, worker and Angular CLI.

The studio is at [localhost:4200](http://localhost:4200), the API docs at [localhost:8000/docs](http://localhost:8000/docs), and readiness at [localhost:8000/health/ready](http://localhost:8000/health/ready). Local development uses one implicit local workspace. Set `LOCAL_SESSION=false` to exercise account registration and login. Both services read the root `.env` with overrides from `backend/.env`. Process environment variables take precedence for settings other than `LLM_API_URL` and `LLM_MODEL_ID`, which are read from the `.env` files.

The Angular dev server reloads frontend changes. Restart the supervisor after changing backend code or configuration.

Each generation job has a cumulative token budget (`AI_JOB_TOKEN_BUDGET`, default 90,000). Planning, slide generation, reviews and validation retries all draw from it. **Retry generation** preserves both completed slides and usage. To give a saved job more room, set a higher total in `backend/.env`, restart the worker and retry the presentation. The worker setting also applies to other jobs it processes.

The worker limits each reply by stage and by the budget left after estimating the request input. Outline limits scale with the requested slide count. On OpenRouter, the worker requests non-thinking mode for DeepSeek V4 models. Inspect `usage.by_stage` at `GET /api/v1/jobs/{job_id}` for call counts, input and output tokens, validation failures, and truncations. Where reported, `reasoning` is included in output. Older checkpoints retain their original totals and accumulate stage details for new calls.

## Branded templates and export review

Open **Skill library → New skill → Branded PPTX** and select a `.pptx` with title or body placeholders defined in Slide Master view. FrameCraft uses the template aspect ratio, theme and placeholder positions during generation. The browser canvas approximates the theme and slot positions. The PowerPoint export uses the native masters, backgrounds and layout artwork, including the original layout names. Ordinary slide artwork and text in the uploaded sample deck stay out of the generated slides. Move recurring brand artwork to the slide master or layouts before importing. Templates support 16:9, 4:3 and square slides.

Install LibreOffice Impress for inherited master artwork in the editor, branded PDF exports, and **Export → Review PowerPoint export**. FrameCraft discovers `soffice` on the server's PATH and checks the standard Windows installation folders. Set `POWERPOINT_RENDERER` to the full executable path if needed. On Windows, use `soffice.com`. Install the fonts used in your templates on the same machine. The PowerPoint export always retains the native master and layouts. Without Impress, the editor and PDF export use a simplified preview of the slide content. The review displays the Impress render, flags potential text placement and font issues, and links to the exact reviewed file. It uses no model provider. Renderer-specific differences are still possible when the deck opens in Microsoft PowerPoint.

## Model setup

Set `LLM_API_URL`, `LLM_MODEL_ID` and `LLM_API_KEY` in `backend/.env` and restart the API and worker. The URL determines the request format. Generation and conversational editing use the same model. Conversational editing requires a live provider. Set `LLM_VISION_ENABLED=true` when the model supports image inputs to let it use uploaded image references for art direction and review rendered slide previews during generation.

| Service | Example `LLM_API_URL` | `LLM_API_KEY` |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | OpenAI key |
| Anthropic | `https://api.anthropic.com/v1` | Anthropic key |
| Gemini | `https://generativelanguage.googleapis.com/v1beta` | Gemini key |
| OpenRouter | `https://openrouter.ai/api/v1` | OpenRouter key |
| Ollama | `http://127.0.0.1:11434` | Optional |
| OpenAI-compatible | Your API root | Optional, depending on the server |

For OpenAI-compatible APIs, Anthropic and Ollama, you can also use the full endpoint URL. For native Ollama on a different port, use the full `/api/chat` URL and enter the installed model tag as `LLM_MODEL_ID`. Set `COMPATIBLE_STRUCTURED=true` when the compatible server supports the OpenAI JSON Schema response format.

Set `RESEARCH_API_KEY` in `.env` to make **Web research** checked by default in the studio. `RESEARCH_API_URL` defaults to `https://api.search.brave.com/res/v1/web/search` and also accepts `BRAVE_WEB_SEARCH` as an environment variable alias. If you override the URL, use a full endpoint that accepts Brave Search requests and returns the same response format. Optionally set `RESEARCH_CONTEXT_API_URL=https://api.search.brave.com/res/v1/llm/context` to try Brave Context first for detailed research and fall back to the Web Search endpoint if needed. Search uses snippets and titles, and references are linked to slides when selected by the model.

With **Web research** enabled, requests for photos or portraits also use Brave Image Search. FrameCraft embeds a preview-sized copy and links the original page in the slide sources. The same key must have Image Search access. You can override the default endpoint with `RESEARCH_IMAGE_API_URL`.

For a **Data Driven** deck or a brief that calls for quantitative evidence, the worker uses configured web research when the request leaves the research setting unspecified and has no reference sources. If Web research was turned off in that situation, the studio asks whether to search or continue without sources. A brief that explicitly limits sources to supplied material keeps external research off.

Set `IMAGE_PROVIDER` to `openai` or `openrouter` in `backend/.env` to enable **Generate illustrations**. Use `IMAGE_GEN_API_KEY` for the selected provider. You can leave it blank if `LLM_API_URL` points to the same provider and `LLM_API_KEY` is set. Existing OpenAI configurations can still use `IMAGE_OPENAI_API_KEY` as a fallback.

| `IMAGE_PROVIDER` | Default image generation URL | Default `IMAGE_MODEL` |
| --- | --- | --- |
| `openai` | `https://api.openai.com/v1/images/generations` | `gpt-image-1.5` |
| `openrouter` | `https://openrouter.ai/api/v1/images` | `bytedance-seed/seedream-4.5` |

Leave `IMAGE_GEN_URL` blank to use the provider default, or set it to the full image generation endpoint. If you override `IMAGE_MODEL` for OpenRouter, choose a raster image model supported by its dedicated Images API. Set `IMAGE_LIMIT_PER_JOB` and `IMAGE_COST_USD` to control the per-deck limit and cost estimate. Restart the API and worker after changing these settings. [OpenAI Images API](https://developers.openai.com/api/reference/resources/images/methods/generate) · [OpenRouter image generation guide](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)

## Manual startup

From `backend` create a virtual environment with `python -m venv .venv`. Activate it using `.venv\Scripts\Activate.ps1` on Windows PowerShell or `source .venv/bin/activate` on macOS/Linux. Run these commands in the backend directory.

For a fresh manual setup, copy `backend/.env.example` to `backend/.env` and configure your provider before starting the services.

```sh
pip install -e ".[dev]"
alembic upgrade head
python -m app.cli seed
uvicorn app.main:app --reload --port 8000
```

From a second backend terminal, start `python -m app.worker`. From `frontend`, run `npm ci` and `npm start`.

## Verification

Run `python -m pytest` from `backend` with its virtual environment activated. From `frontend`, run `npm test` and `npm run build`. To run the browser suite after setup, run `npx playwright install chromium` and `npm run e2e`. For an installed Edge or Chrome browser, set `STUDIO_BROWSER_CHANNEL=msedge` or `chrome` and skip the Chromium install.

Playwright starts the API, worker and Angular on ports 8000 and 4200 with temporary SQLite and file storage and test-only model responses. Free both ports before running it. `STUDIO_E2E_REUSE=1` allows Playwright to reuse an existing server at port 4200, and the suite writes to that server's workspace. To include the branded PowerPoint review browser test, set `POWERPOINT_RENDERER` in the shell that launches Playwright, even if the application already finds Impress.

With the default settings and backend working directory, SQLite uses `backend/data/app.db` and stored objects use `backend/data/uploads`. Stop both Python processes before copying `backend/data` for a consistent local backup.
