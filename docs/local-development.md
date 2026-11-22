# Local development

Install Git, Python 3.11 or later, and Node 20.19+, 22.13+, or 24.x with npm. Run `python scripts/setup.py` followed by `python scripts/dev.py` from the repository root. The setup script creates a virtual environment, installs dependencies, applies migrations and seeds the skill library. The development supervisor starts the API, worker and Angular CLI.

The studio is at [localhost:4200](http://localhost:4200), the API docs at [localhost:8000/docs](http://localhost:8000/docs), and readiness at [localhost:8000/health/ready](http://localhost:8000/health/ready). Local development uses one implicit local workspace. Set `LOCAL_SESSION=false` to exercise account registration and login. Both the worker and API read the same configuration. For `LLM_API_URL` and `LLM_MODEL_ID`, the services read `backend/.env` first and fall back to the root `.env`. Other variables can also be set in the process environment, which takes precedence for those variables.

The Angular dev server reloads frontend changes. Restart the supervisor after changing backend code or configuration.

## Branded templates and export review

Open **Skill library → New skill → Branded PPTX** and select a `.pptx` with title or body placeholders defined in Slide Master view. FrameCraft uses the template aspect ratio, theme and placeholder positions during generation. The browser canvas approximates the theme and slot positions. The PowerPoint export uses the native masters, backgrounds and layout artwork, including the original layout names. Ordinary slide artwork and text in the uploaded sample deck stay out of the generated slides. Move recurring brand artwork to the slide master or layouts before importing. Templates support 16:9, 4:3 and square slides.

Install LibreOffice Impress for **Export → Review PowerPoint export**. FrameCraft discovers `soffice` on the server's PATH and checks the standard Windows installation folders. Set `POWERPOINT_RENDERER` to the full executable path if needed. On Windows, use `soffice.com`. Install the fonts used in your templates on the same machine. The review displays the Impress render, flags potential text placement and font issues, and links to the exact reviewed file. It uses no model provider. Renderer-specific differences are still possible when the deck opens in Microsoft PowerPoint.

## Model setup

Set `LLM_API_URL`, `LLM_MODEL_ID` and `LLM_API_KEY` in `backend/.env` and restart the API and worker. The URL determines the request format. Generation and conversational editing use the same model. Set `LLM_VISION_ENABLED=true` to enable screenshot review when that model supports images.

| Service | Example `LLM_API_URL` | `LLM_API_KEY` |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | OpenAI key |
| Anthropic | `https://api.anthropic.com/v1` | Anthropic key |
| Gemini | `https://generativelanguage.googleapis.com/v1beta` | Gemini key |
| OpenRouter | `https://openrouter.ai/api/v1` | OpenRouter key |
| Ollama | `http://127.0.0.1:11434` | Optional |
| OpenAI-compatible | Your API root | Optional, depending on the server |
| Offline demo | `demo` | Leave blank |

For OpenAI-compatible APIs, Anthropic and Ollama, you can also use the full endpoint URL. For native Ollama on a different port, use the full `/api/chat` URL and enter the installed model tag as `LLM_MODEL_ID`. Set `COMPATIBLE_STRUCTURED=true` when the compatible server supports the OpenAI JSON Schema response format. Leave `LLM_MODEL_ID` blank for the offline demo.

Set `RESEARCH_API_KEY` in `.env` to make **Web research** checked by default in the studio. `RESEARCH_API_URL` defaults to `https://api.search.brave.com/res/v1/web/search` and also accepts `BRAVE_WEB_SEARCH` as an environment variable alias. If you override the URL, use a full endpoint that accepts Brave Search requests and returns the same response format. Optionally set `RESEARCH_CONTEXT_API_URL=https://api.search.brave.com/res/v1/llm/context` to try Brave Context first for detailed research and fall back to the Web Search endpoint if needed. Search uses snippets and titles, and references are linked to slides when selected by the model.

With **Web research** enabled, requests for photos or portraits also use Brave Image Search. FrameCraft embeds a preview-sized copy and links the original page in the slide sources. The same key must have Image Search access. You can override the default endpoint with `RESEARCH_IMAGE_API_URL`.

Set `IMAGE_PROVIDER` to `openai` or `openrouter` in `backend/.env` to enable **Generate illustrations**. Use `IMAGE_GEN_API_KEY` for the selected provider. You can leave it blank if `LLM_API_URL` points to the same provider and `LLM_API_KEY` is set. Existing OpenAI configurations can still use `IMAGE_OPENAI_API_KEY` as a fallback.

| `IMAGE_PROVIDER` | Default image generation URL | Default `IMAGE_MODEL` |
| --- | --- | --- |
| `openai` | `https://api.openai.com/v1/images/generations` | `gpt-image-1.5` |
| `openrouter` | `https://openrouter.ai/api/v1/images` | `bytedance-seed/seedream-4.5` |

Leave `IMAGE_GEN_URL` blank to use the provider default, or set it to the full image generation endpoint. If you override `IMAGE_MODEL` for OpenRouter, choose a raster image model supported by its dedicated Images API. Set `IMAGE_LIMIT_PER_JOB` and `IMAGE_COST_USD` to control the per-deck limit and cost estimate. Restart the API and worker after changing these settings. [OpenAI Images API](https://developers.openai.com/api/reference/resources/images/methods/generate) · [OpenRouter image generation guide](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)

## Manual startup

From `backend` create a virtual environment with `python -m venv .venv`. Activate it using `.venv\Scripts\Activate.ps1` on Windows PowerShell or `source .venv/bin/activate` on macOS/Linux. Run these commands in the backend directory.

```sh
pip install -e ".[dev]"
alembic upgrade head
python -m app.cli seed
uvicorn app.main:app --reload --port 8000
```

From a second backend terminal, start `python -m app.worker`. From `frontend`, run `npm ci` and `npm start`.

## Verification

Run `python -m pytest` in an activated backend environment. From `frontend`, run `npm test`, `npm run build`, `npx playwright install chromium` and `npm run e2e`. Playwright starts isolated services on the standard local ports, so stop other studio processes first. For an installed Edge or Chrome browser, set `STUDIO_BROWSER_CHANNEL=msedge` or `chrome` to use that browser. `STUDIO_E2E_REUSE=1` uses existing services and creates presentations in that workspace.

SQLite data is stored under `backend/data` by default. Stop both Python processes to copy the entire data directory as a consistent local backup.
