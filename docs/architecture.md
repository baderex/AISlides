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

Series and marks use editable PowerPoint shapes and labels. Sankey links use proportional freeform ribbons, with a balance check at each intermediate node. Dense graphs use Grandalf for hierarchical layers and ordering. Matrix exhibits use native tables with cell colors, weights and recommendation markers where applicable. Quantitative exhibits carry a visible evidence status. The slide specification records source observation IDs, input values and calculation dependencies. PowerPoint speaker notes and PDF annotations carry the same lineage. Qualitative heatmaps use ordered categories to define color meaning.

Consulting uses the shared composer and slide specifications. Its profile supplies defaults for density, typography, headlines, and exhibits. Style presets supply defaults that prompts can override. A skill manifest may also define a `theme.brand_kit` with protected theme roles and the ID of an uploaded logo. A corporate PowerPoint template retains its master, placeholders, and aspect ratio.

Semantic keys identify editable objects across recipes. Restyling applies field-level content and style edits to matching objects. It also carries through geometry edits to those objects when the recipe and slide layout stay the same. Older slides with manual edits use the existing style-only update when their element identities cannot be matched reliably. The workspace's Edit exhibit dialog changes chart inputs, matrix scores, graph relationships and schedule dependencies, then rebuilds the slide for review and saving.

Layout review first checks technical fit and exhibit completeness. It then evaluates hierarchy, emphasis, balance, imagery, and fit to the requested style. Ranked bars, waterfalls, decision matrices, architecture diagrams, roadmaps and Sankey diagrams have alternate compositions when the exhibit content supports them. When vision is enabled, important slides can compare eligible renders using that rubric. The local rubric remains available without vision.

Evidence extraction records source IDs, quoted observations, periods, scales, and scope. Reconciliation checks observations against their cited excerpts before using them and retains the original text parser for supported text-only patterns. Conflicting values or scopes produce visible review notes.

See the [agent workflow](agent-workflow.md), [data model](data-model.md) and [production deployment guide](production-deployment.md).

## Reference review

- Presenton informed server-side provider configuration, document intake and REST-driven self-hosting. [Presenton](https://github.com/presenton/presenton)
- [PPT Master](https://github.com/hugohe3/ppt-master) (MIT) informed explicit design recipes and the priority on native PowerPoint objects. SVG conversion would add a second layout representation.
- [PPTAgent](https://github.com/icip-cas/PPTAgent) (MIT) informed research before planning, separate slide review and bounded revision. The worker uses explicit SQL checkpoints to support the infrastructure-free local workflow.
- [Slidev](https://github.com/slidevjs/slidev) and [Marp](https://github.com/marp-team/marp) (MIT) informed declarative themes and reproducible previews. Their Markdown and browser pipelines serve different editing models.

The implementation adopts patterns from these projects and uses independent source code. See the [export ADR](adr/0001-powerpoint-export.md) for the export engine decision.

The source review examined Presenton’s [provider settings service](https://github.com/presenton/presenton/blob/main/servers/fastapi/services/provider_settings.py), PPT Master’s [DrawingML converter](https://github.com/hugohe3/ppt-master/blob/main/skills/ppt-master/scripts/svg_to_pptx/drawingml/converter.py), PPTAgent’s [visual review adapter](https://github.com/icip-cas/PPTAgent/blob/main/skills/pptagent/scripts/pptagent_runtime/visual.py), Slidev’s [slide parser](https://github.com/slidevjs/slidev/blob/main/packages/parser/src/core.ts), and Marp’s [rendering core](https://github.com/marp-team/marp-core/blob/main/src/marp.ts).
