# Agent workflow

The worker analyzes the prompt and references, asks for clarification when needed, researches, chooses an art direction, and plans the deck. It then drafts, composes and reviews one slide at a time. If a checkpoint needs an answer, the worker parks the job and frees the queue. Answer submission validates the saved question batch and requeues the job at the checkpoint recorded for that pause.

Provider responses are schema-validated. Source material is supplied as untrusted data and never grants tools or authority. Deterministic layout, rendering and export consume the presentation IR.

New live prompt decks use the HTML/CSS compositor. After clarification, the worker saves a palette and outline, requests one slide of declarative markup, and checkpoints the candidate before opening Chromium. Bundled rendering code composes the markup offline. The worker measures each object, validates the slide and commits the editable geometry and PNG preview together. A rejected layout can receive two automatic corrections. Uploaded pictures can reference only assets attached to the job. The model token and cost budgets continue across checkpoints and retries.

The native compositor handles PowerPoint masters, protected brand kits, generated or web-sourced imagery, charts requiring an embedded workbook, quantitative decks with researched or uploaded documentary evidence, and the demo fixture. Its design checkpoint saves the art direction before requesting the outline. Pre-upgrade jobs continue from their original checkpoints. The following exhibit and vision-review steps apply to this compositor.

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
