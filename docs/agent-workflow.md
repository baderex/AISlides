# Agent workflow

The worker analyzes the prompt and references, asks for clarification when needed, researches, chooses an art direction, and plans the deck. It then drafts, composes and reviews one slide at a time. If a checkpoint needs an answer, the worker parks the job and frees the queue. Answer submission validates the saved question batch and requeues the job at the checkpoint recorded for that pause.

Provider responses are schema-validated. Source material is supplied as untrusted data and never grants tools or authority. Deterministic layout, rendering and export consume the presentation IR.

New live prompt decks use the HTML/CSS compositor. Both compositors save the deck art direction before planning the outline. The design specification provides role-based type sizes in points, semantic colors, spacing, composition and imagery. The HTML designer also receives the recent slides’ measured headline positions and composition patterns. The worker checkpoints the markup before opening Chromium and commits the selected editable geometry with its preview.

HTML slides have up to two correction attempts. Clipping, severe text overlap, unreadable contrast and missing assigned visuals require repair. Hierarchy, whitespace and repeated compositions can trigger a second candidate. A visual alternative must preserve the content and improve the score to replace the first acceptable layout. With LibreOffice available, the worker also exports and renders the candidate PowerPoint, checks text and tables in their frames, and compares rendered regions with the canonical preview. The model token and cost budgets apply across retries.

The editorial preset pairs Source Serif 4 and Manrope. IBM Plex Mono is available for code. The editor and offline worker load local static regular and bold faces, matching the slide model’s supported weights. PDF uses these font files and PowerPoint embeds the bundled families used on a slide. Each family ships with its SIL Open Font License in the worker’s fonts directory.

The HTML path applies the selected skill and protected brand roles, places assigned pictures, and can reserve a measured region for a structured exhibit or workbook-backed chart. The native compositor handles PowerPoint masters and quantitative decks using web research or uploaded documents. The following exhibit and vision-review steps describe the native workflow.

The outline can record a slide's topic, intended takeaway and whether the title was supplied verbatim. Before composition, the worker uses the brief and cited excerpts to reconcile recognized figures in charts, tables and financial readouts. It can derive a headline from those figures and falls back to a neutral title if it cannot support a numeric claim. Exact titles supplied in the brief are preserved when recognized.

A source conflict can trigger one additional question before planning if earlier answers do not resolve it. Data-driven briefs can also pause to ask about web research when quantitative evidence is missing. The worker can place an uploaded image, retrieve a requested photo through configured image search, or generate an illustration on an eligible slide when enabled. It fits and validates text and tables, renders a PNG, and can request one content rewrite per slide when vision review is enabled. The slide, preview record and completion event commit before the next checkpoint.

The queue checks the lease token on each commit. A worker can reclaim a running checkpoint after its lease expires or its worker heartbeat becomes stale. Failed jobs can retry from the saved checkpoint. Cancelling a job invalidates its lease so any pending provider response is discarded before it can change the deck.

## Design benchmarks

The four benchmark briefs cover an executive decision in 16:9, a dense financial review in 4:3, an image-led product launch in 16:9, and a technical explainer in 1:1. Run them with the backend Python environment and its configured live provider.

```sh
python scripts/benchmark_decks.py --output data/quality-benchmark/new-run --launch-image path/to/lantern-reference.jpg
```

The runner uses an isolated database and exports each deck as PDF and editable PowerPoint. It saves a preview for each slide and a run report with generation warnings, native object checks, and PDF findings. Use a fresh output directory for each run. Pass `--case` to select a brief.

The launch fixture uses [Working By Emergency Light](https://www.flickr.com/photos/cogdog/4252388319/) by Alan Levine (cogdogblog), licensed under [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/). Use the same image for each comparison. Review the exported slides at the same scale and check the contents against the briefs in [benchmark_briefs.json](../scripts/benchmark_briefs.json).
