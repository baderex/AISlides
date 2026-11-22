# Agent workflow

Generation moves through created, analyzing, waiting for user, researching, planning, generating, reviewing and completed. Waiting for user returns control to the queue. Answer submission validates the persisted question batch and moves the job to its next checkpoint. A reclaimed job reads its existing plan and appends slides only after review and verification of lease ownership.

Provider responses are schema-validated. Source material is supplied as untrusted data and never grants tools or authority. Deterministic layout, rendering and export consume the presentation IR.

After clarification and research, the design checkpoint asks the model for a typed art direction. The worker saves the palette, typography, imagery, and component rules on the document before requesting the outline. Selected template colors and fonts take precedence. Composition uses the actual slide content to choose an exhibit, then resolves its styles into editable elements shared by the editor, preview, PDF, and PowerPoint writers. Older documents without design rules retain the original composition path.

A source conflict can trigger one additional question before planning if earlier answers do not resolve it. The worker can attach a generated illustration to an eligible slide when image generation is enabled. It previews each slide, fits text, and can use the configured model for at most one content rewrite when vision review is enabled. Validated previews and slides commit with their event before the next checkpoint.

The queue fences commits by lease token and recovers work when a worker heartbeat disappears. A cancelled in-flight provider request can still finish at the provider. Its expired lease prevents it from writing new slides.

## Design benchmarks

The four benchmark briefs cover an executive decision in 16:9, a dense financial review in 4:3, an image-led product launch in 16:9, and a technical explainer in 1:1. Run them with the backend Python environment and its configured live provider.

```sh
python scripts/benchmark_decks.py --output data/quality-benchmark/new-run --launch-image path/to/lantern-reference.jpg
```

The runner uses an isolated database and exports each deck as PDF and editable PowerPoint. It saves a preview for each slide and a run report with generation warnings, native object checks, and PDF findings. Use a fresh output directory for each run. Pass `--case` to select a brief.

The launch fixture uses [Working By Emergency Light](https://www.flickr.com/photos/cogdog/4252388319/) by Alan Levine (cogdogblog), licensed under [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/). Use the same image for each comparison. Review the exported slides at the same scale and check the contents against the briefs in [benchmark_briefs.json](../scripts/benchmark_briefs.json).
