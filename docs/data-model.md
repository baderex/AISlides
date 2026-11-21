# Data model

Presentations belong to a workspace user. The presentation row holds the canonical versioned IR, request settings, lifecycle state and optimistic revision. Immutable snapshots preserve saved revisions. The IR uses normalized dimensions with independent physical slide sizes and discriminated element types.

Generation jobs carry the next checkpoint and lease. Events are append-only and replayable by numeric cursor. Questions and submitted answers are persisted in the job checkpoint. Assets are scoped to their owner and stored by generated key. The skills catalog combines shared seed recipes and user-owned declarative manifests.

Preview records identify the rendered IR fingerprint and connect a slide to a stored PNG. Manual edits invalidate the preview by changing the fingerprint. The current slide then renders on demand. Export records associate artifact objects with the presentation revision and format.
