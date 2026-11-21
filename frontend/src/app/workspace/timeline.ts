import { DatePipe } from '@angular/common';
import { Component, computed, input } from '@angular/core';
import { icons, StudioIcon } from '../icons';
import { StudioEvent } from '../models';

type TimelineEntry =
  | { id: string; kind: 'thinking'; events: StudioEvent[] }
  | { id: string; kind: 'message' | 'warning'; event: StudioEvent };

const ROUTINE_EVENTS = new Set([
  'generation.started', 'generation.paused', 'generation.resumed', 'question.requested',
  'agent.started', 'agent.progress', 'slide.started', 'slide.revised', 'slide.completed',
]);

function isRoutine(event: StudioEvent): boolean {
  return ROUTINE_EVENTS.has(event.type) ||
    (event.type === 'research.completed' && !event.payload.sources?.length && !event.payload.uncertainties?.length);
}

function groupActivity(events: readonly StudioEvent[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  for (const event of events) {
    if (!isRoutine(event)) {
      entries.push({ id: 'message-' + event.id, kind: 'message', event });
      continue;
    }
    const previous = entries.at(-1);
    if (previous?.kind === 'thinking') {
      previous.events.push(event);
    } else {
      entries.push({ id: 'thinking-' + event.id, kind: 'thinking', events: [event] });
    }
    if (event.payload.warnings?.length) {
      entries.push({ id: 'warning-' + event.id, kind: 'warning', event });
    }
  }
  return entries;
}

@Component({
  selector: 'studio-timeline',
  imports: [DatePipe, StudioIcon],
  templateUrl: './timeline.html',
})
export class Timeline {
  readonly events = input.required<readonly StudioEvent[]>();
  readonly entries = computed(() => groupActivity(this.events()));
  readonly i = icons;
}
