# Studio architecture

Angular 21 serves a zoneless client with lazy dashboard and workspace routes. FastAPI exposes a versioned API. A separate Python worker drives generation using a SQL-backed state machine. SQLite and filesystem storage are the native defaults. PostgreSQL, Redis and S3 are production adapters.

## Reference review

- [Presenton](https://github.com/presenton/presenton) (Apache-2.0) informed the provider selector, document intake and REST-driven self-hosting. Its Next.js and template runtime are outside the Angular and typed-IR requirements.
- [PPT Master](https://github.com/hugohe3/ppt-master) (MIT) informed explicit design recipes and the priority on native PowerPoint objects. SVG conversion would add a second layout representation.
- [PPTAgent](https://github.com/icip-cas/PPTAgent) (MIT) informed research before planning, separate slide review and bounded revision. The worker uses explicit SQL checkpoints to support the infrastructure-free local workflow.
- [Slidev](https://github.com/slidevjs/slidev) and [Marp](https://github.com/marp-team/marp) (MIT) informed declarative themes and reproducible previews. Their Markdown and browser pipelines serve different editing models.

The implementation adopts patterns from these projects and uses independent source code. See the [export ADR](adr/0001-powerpoint-export.md) for the export engine decision.

The source review examined Presenton’s [provider settings service](https://github.com/presenton/presenton/blob/main/servers/fastapi/services/provider_settings.py), PPT Master’s [DrawingML converter](https://github.com/hugohe3/ppt-master/blob/main/skills/ppt-master/scripts/svg_to_pptx/drawingml/converter.py), PPTAgent’s [visual review adapter](https://github.com/icip-cas/PPTAgent/blob/main/skills/pptagent/scripts/pptagent_runtime/visual.py), Slidev’s [slide parser](https://github.com/slidevjs/slidev/blob/main/packages/parser/src/core.ts), and Marp’s [rendering core](https://github.com/marp-team/marp-core/blob/main/src/marp.ts).
