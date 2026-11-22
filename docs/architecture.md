# Studio architecture

Angular 21 serves a zoneless client with lazy dashboard and workspace routes. FastAPI exposes a versioned API. A separate Python worker drives generation using a SQL-backed state machine. SQLite and filesystem storage are the native defaults. PostgreSQL, Redis and S3 are production adapters.

- The browser uses `/api/v1` on the same origin for uploads, editing, exports, and server-sent events. The Angular development proxy forwards API and health requests to FastAPI. Production serves the built client behind an HTTPS reverse proxy.
- The API owns sessions, validates workspace access and revisions, and queues generation. The worker claims jobs from SQL and commits one checkpoint at a time. Optional Redis dispatch provides job IDs to prioritize, and SQL polling recovers missed dispatches.
- The worker plans content and design with the configured model and composes slides into a typed intermediate representation (IR). The Angular canvas, PNG renderer, PDF writer and PowerPoint writer consume that representation. Optional export review renders the actual PPTX through LibreOffice Impress.

## Semantic exhibits

An outline slide can declare `slideIntent`, `mainMessage`, a `visual` type and optional variant, and short `annotations`. The content pass fills in the selected exhibit's data and relationships. The composer fits them to the slide, applying the current theme and available template space. Models supply chart values, node IDs, role names, or time periods. The composer derives slide geometry.

| Intent | Exhibit choices |
| --- | --- |
| Compare, reconcile, or analyze values | Ranked bar, waterfall, funnel, slope, tornado, Pareto |
| Show composition or weighted flows | Stacked bar, Mekko, Sankey |
| Score, prioritize, or assign responsibility | Heatmap, decision and scoring matrices, two-by-two, bubble, RACI, capability map |
| Explain causes, relationships, or organization | Issue and driver trees, value chain, Venn, organization chart, architecture, ER, network topology, layered stack |
| Explain processes, dependencies, and interactions | Flowchart, data flow, state machine, pipeline/DAG, sequence, swimlane, timing diagram, flow diagram with a two-scenario comparison |
| Show rollout plans or implementation details | Roadmap, Gantt, annotated code |

Series and marks use editable PowerPoint shapes and labels. Matrix exhibits use native tables with cell colors, weights and recommendation markers where applicable. The composer also supports annotation arrows, callout panels, kickers, key metrics, deltas, status markers, rich-text emphasis, highlight bands, source notes, footnotes, and legends. Quantitative exhibits require a cited source. For a qualitative heatmap, ordered categories define color meaning directly.

See the [agent workflow](agent-workflow.md), [data model](data-model.md) and [production deployment guide](production-deployment.md).

## Reference review

- Presenton informed server-side provider configuration, document intake and REST-driven self-hosting. [Presenton](https://github.com/presenton/presenton)
- [PPT Master](https://github.com/hugohe3/ppt-master) (MIT) informed explicit design recipes and the priority on native PowerPoint objects. SVG conversion would add a second layout representation.
- [PPTAgent](https://github.com/icip-cas/PPTAgent) (MIT) informed research before planning, separate slide review and bounded revision. The worker uses explicit SQL checkpoints to support the infrastructure-free local workflow.
- [Slidev](https://github.com/slidevjs/slidev) and [Marp](https://github.com/marp-team/marp) (MIT) informed declarative themes and reproducible previews. Their Markdown and browser pipelines serve different editing models.

The implementation adopts patterns from these projects and uses independent source code. See the [export ADR](adr/0001-powerpoint-export.md) for the export engine decision.

The source review examined Presenton’s [provider settings service](https://github.com/presenton/presenton/blob/main/servers/fastapi/services/provider_settings.py), PPT Master’s [DrawingML converter](https://github.com/hugohe3/ppt-master/blob/main/skills/ppt-master/scripts/svg_to_pptx/drawingml/converter.py), PPTAgent’s [visual review adapter](https://github.com/icip-cas/PPTAgent/blob/main/skills/pptagent/scripts/pptagent_runtime/visual.py), Slidev’s [slide parser](https://github.com/slidevjs/slidev/blob/main/packages/parser/src/core.ts), and Marp’s [rendering core](https://github.com/marp-team/marp-core/blob/main/src/marp.ts).
