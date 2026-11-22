# Agent workflow

The worker analyzes the prompt and references, asks for clarification when needed, researches, chooses an art direction, and plans the deck. It then drafts, composes and reviews one slide at a time. If a checkpoint needs an answer, the worker parks the job and frees the queue. Answer submission validates the saved question batch and requeues the job at the checkpoint recorded for that pause.

Provider responses are schema-validated. Source material is supplied as untrusted data and never grants tools or authority. Deterministic layout, rendering and export consume the presentation IR.

After clarification and research, the design checkpoint asks the model for a typed art direction. The worker saves palette, typography, imagery and component rules on the document before requesting the outline. The selected skill or consulting profile supplies the base theme when either applies. For standard, unbranded decks, planning can assign an exhibit such as a financial readout, a stacked comparison or a flow or branching diagram. The worker validates the draft against the planned exhibit and composes its content into editable slide elements.

A source conflict can trigger one additional question before planning if earlier answers do not resolve it. Data-driven briefs can also pause to ask about web research when quantitative evidence is missing. The worker can place an uploaded image, retrieve a requested photo through configured image search, or generate an illustration on an eligible slide when enabled. It fits and validates text and tables, renders a PNG, and can request one content rewrite per slide when vision review is enabled. The slide, preview record and completion event commit before the next checkpoint.

The queue checks the lease token on each commit. A worker can reclaim a running checkpoint after its lease expires or its worker heartbeat becomes stale. Failed jobs can retry from the saved checkpoint. Cancelling a job invalidates its lease so any pending provider response is discarded before it can change the deck.

## Design benchmarks

The four benchmark briefs cover an executive decision in 16:9, a dense financial review in 4:3, an image-led product launch in 16:9, and a technical explainer in 1:1. Run them with the backend Python environment and its configured live provider.

```sh
python scripts/benchmark_decks.py --output data/quality-benchmark/new-run --launch-image path/to/lantern-reference.jpg
```

The runner uses an isolated database and exports each deck as PDF and editable PowerPoint. It saves a preview for each slide and a run report with generation warnings, native object checks, and PDF findings. Use a fresh output directory for each run. Pass `--case` to select a brief.

The launch fixture uses [Working By Emergency Light](https://www.flickr.com/photos/cogdog/4252388319/) by Alan Levine (cogdogblog), licensed under [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/). Use the same image for each comparison. Review the exported slides at the same scale and check the contents against the briefs in [benchmark_briefs.json](../scripts/benchmark_briefs.json).
