# Implementation checklist

- [x] Foundation with Angular, FastAPI, provider configuration, session scoping and skills.
- [x] Typed clarification and a resumable local worker with replayable events.
- [x] Canonical IR, partial slides, native PowerPoint, PDF and stored PNG generation previews.
- [x] Canvas editing, version history and automated PowerPoint round-trip verification.
- [x] Reference grounding, optional search, layout validation and conversational edit proposals.
- [x] Native setup, tests and deployment documentation.
- [x] Import reusable branded PowerPoint layouts and review the rendered PowerPoint export.
- [x] Prompt-aware art direction, editable diagram exhibits, native stacked charts and styled financial tables.
- [ ] Run a staging deployment against PostgreSQL, Redis and an S3-compatible server.
- [ ] Exercise credentialed hosted providers, web research and vision against live services.

Earlier local acceptance testing used Edge and a Qwen model behind llama.cpp’s OpenAI-compatible endpoint. It covered generation, resuming after a restart and a structured edit proposal. Desktop Microsoft PowerPoint opened and rendered the reviewed three-slide deck with editable text. The current automated suites cover the browser workflow with the demo provider and reopen generated PowerPoint packages to check native text, shapes, connectors, chart data, tables, media and notes. The [design benchmark runner](agent-workflow.md#design-benchmarks) supports additional live-provider checks across four briefs.

## Current format limits

- Branded PPTX import reuses masters and layouts at PowerPoint export. Ordinary reference uploads supply text. The browser canvas and standard PDF export approximate the branded layout and omit inherited master artwork.
- Direct chart-data editing is available in the exported PowerPoint. The studio can position, resize and delete chart and table elements.
- Browser, PDF and PowerPoint use separate font engines. Advanced grouping transformations and formatting support vary by renderer. See the [PowerPoint export decision](adr/0001-powerpoint-export.md).
