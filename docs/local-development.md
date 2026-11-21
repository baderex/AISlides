# Local development

Install Git, Python 3.11 or later, and Node 20.19+, 22.13+, or 24.x with npm. Run `python scripts/setup.py` followed by `python scripts/dev.py` from the repository root. The setup script creates a virtual environment, installs dependencies, applies migrations and seeds the skill library. The development supervisor starts the API, worker and Angular CLI.

The studio is at [localhost:4200](http://localhost:4200), the API docs at [localhost:8000/docs](http://localhost:8000/docs), and readiness at [localhost:8000/health/ready](http://localhost:8000/health/ready). Local development uses one implicit local workspace. Set `LOCAL_SESSION=false` to exercise account registration and login. Both the worker and API read the same environment. The backend dotenv file takes precedence over the root fallback, and process environment variables take precedence over both.

The Angular dev server reloads frontend changes. Restart the supervisor after changing backend code or configuration.

## Branded templates and export review

Open **Skill library → New skill → Branded PPTX** and select a `.pptx` with title or body placeholders defined in Slide Master view. FrameCraft uses the template aspect ratio, theme and placeholder positions during generation. The browser canvas approximates the theme and slot positions. The PowerPoint export uses the native masters, backgrounds and layout artwork, including the original layout names. Ordinary slide artwork and text in the uploaded sample deck stay out of the generated slides. Move recurring brand artwork to the slide master or layouts before importing. Templates support 16:9, 4:3 and square slides.

Install LibreOffice Impress for **Export → Review PowerPoint export**. FrameCraft discovers `soffice` on the server's PATH and checks the standard Windows installation folders. Set `POWERPOINT_RENDERER` to the full executable path if needed. On Windows, use `soffice.com`. Install the fonts used in your templates on the same machine. The review displays the Impress render, flags potential text placement and font issues, and links to the exact reviewed file. It uses no model provider. Renderer-specific differences are still possible when the deck opens in Microsoft PowerPoint.

## Model setup

| Provider ID | Backend configuration |
| --- | --- |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `gemini` | `GEMINI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `ollama` | Run Ollama, pull a model and set `OLLAMA_BASE_URL` if it uses a non-default port |
| `compatible` | `COMPATIBLE_BASE_URL` ending in the provider’s API root, optional `COMPATIBLE_API_KEY`, and a model ID |
| `demo` | Explicit offline fixtures for verification |

Set `AI_PROVIDER` to pick a default. Use `AI_MODEL` for its global model override, or `PLANNING_MODEL`, `RESEARCH_MODEL`, `SLIDE_MODEL` and `VISION_MODEL` for role-specific models on the selected provider. The model field in the composer overrides the generation model for an individual presentation. Configure a vision-capable `VISION_MODEL` to enable screenshot review. Set `COMPATIBLE_STRUCTURED=true` when the compatible server supports the OpenAI JSON Schema response format.

Set `RESEARCH_API_KEY` in the root `.env` to enable Web research in the studio. `RESEARCH_API_URL` defaults to `https://api.search.brave.com/res/v1/web/search`. If you override it, use the full URL of an endpoint that accepts Brave Search requests and returns the same response format. Search uses snippets and titles, and references are linked to slides when selected by the model.

Enable the optional illustration control with `IMAGE_PROVIDER=openai` and `IMAGE_OPENAI_API_KEY` or `OPENAI_API_KEY`. Configure `IMAGE_MODEL`, `IMAGE_LIMIT_PER_JOB` and `IMAGE_COST_USD` to set the model, per-deck limit and cost estimate. The worker stores generated illustrations as independent image assets. [OpenAI image generation documentation](https://developers.openai.com/api/docs/guides/image-generation)

For native Ollama, run `ollama pull qwen2.5:7b` and set `AI_PROVIDER=ollama`. The application sends JSON Schema to the [Ollama Chat API](https://docs.ollama.com/api/chat). The offline demo is available from Studio settings for machines without a configured model.

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
