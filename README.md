# Framecraft Studio

A self-hosted presentation studio that turns prompts and references into editable slide decks. Angular handles the design workspace. FastAPI and a separate Python worker manage guided generation, saved versions and exports.

HTML/CSS is the default layout for new live prompt decks. Chromium measures the layout, and the worker saves editable text, shapes, connectors, tables, pictures and charts. Structured exhibits keep their source data for editing. The HTML path supports brand kits, generated or sourced pictures, and PowerPoint charts with embedded workbooks. The native compositor handles imported PowerPoint templates and quantitative decks using web research or uploaded documents.

## Run locally

Install Python 3.11 or later, Node.js 22.13 or later in the 22.x series, npm and Git. The [local development guide](docs/local-development.md) lists the other supported Node.js versions.

```sh
python scripts/setup.py
python scripts/dev.py
```

Open [the studio](http://localhost:4200) or [API documentation](http://localhost:8000/docs). Check services with `python scripts/check.py`.

Set `LLM_API_URL`, `LLM_MODEL_ID` and `LLM_API_KEY` in `backend/.env`, then restart the API and worker. The example configuration uses OpenAI.

## Capabilities

- Server-side configuration for OpenAI, Anthropic, Gemini, OpenRouter, Ollama and OpenAI-compatible endpoints.
- Durable worker checkpoints, clarification cards, event replay, cancellation, and retry.
- Reference uploads, optional search, source references, reusable skill manifests and deck version history.
- Branded PowerPoint templates reuse the original slide masters and layouts with editable generated content.
- Slide and element editing, undo and redo, presentation mode, and live-provider conversational edit previews.
- Prompt-aware visual design with editable diagrams, styled tables and native charts. PowerPoint preserves editable text, shapes, connectors, tables, charts, pictures and speaker notes.
- PDF downloads and per-slide PNG previews use the same slide model.
- Optional visual review renders the exported PowerPoint in LibreOffice Impress and flags potential clipping, overlapping text and font changes.

The local application uses SQLite and filesystem storage. Production configuration supports PostgreSQL, Redis job dispatch, and S3-compatible storage. See the implementation checklist for verification status and known gaps.

## Documentation

- [Local development](docs/local-development.md)
- [Production deployment](docs/production-deployment.md)
- [Architecture](docs/architecture.md)
- [Data model](docs/data-model.md)
- [Agent workflow](docs/agent-workflow.md)
- [Implementation checklist](docs/implementation-plan.md)
- [PowerPoint export decision](docs/adr/0001-powerpoint-export.md)

## Acknowledgments

Thanks to the maintainers and contributors of the open-source projects that helped shape Framecraft Studio.

- Slide design recipes and editable PowerPoint exports with native objects. [PPT Master](https://github.com/hugohe3/ppt-master)
- AI provider configuration, document uploads, and a self-hosted REST API. [Presenton](https://github.com/presenton/presenton)
- Research before slide planning, a dedicated review step, and a limit on revision rounds. [PPTAgent](https://github.com/icip-cas/PPTAgent)
- Declarative slide themes and reproducible previews. [Slidev](https://github.com/slidevjs/slidev) and [Marp](https://github.com/marp-team/marp)

For more detail, see the [architecture reference review](docs/architecture.md#reference-review).
