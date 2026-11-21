# Agent workflow

Generation moves through created, analyzing, waiting for user, researching, planning, generating, reviewing and completed. Waiting for user returns control to the queue. Answer submission validates the persisted question batch and moves the job to its next checkpoint. A reclaimed job reads its existing plan and appends slides only after review and verification of lease ownership.

Provider responses are schema-validated. Source material is supplied as untrusted data and never grants tools or authority. Deterministic layout, rendering and export consume the presentation IR.

In guided mode a source conflict can trigger one additional question before planning. The worker can attach a generated illustration to an eligible slide when image generation is enabled. It previews each slide, fits text, and can use an optional vision model for at most one content rewrite. Validated previews and slides commit with their event before the next checkpoint.

The queue fences commits by lease token and recovers work when a worker heartbeat disappears. A cancelled in-flight provider request can still finish at the provider. Its expired lease prevents it from writing new slides.
