import { afterRenderEffect, Component, computed, ElementRef, input, signal, viewChild } from '@angular/core';
import { icons, StudioIcon } from '../icons';
import { Status, StudioEvent } from '../models';

export function generationPlan(events: readonly StudioEvent[], status: Status) {
  const intent = events.find(event => event.type === 'agent.progress');
  const sources = events.find(event => event.type === 'research.completed');
  const outline = events.find(event => event.type === 'outline.completed')?.payload.outline;
  const question = events.filter(event => event.type === 'question.requested').at(-1);
  const resume = events.filter(event => event.type === 'generation.resumed').at(-1);
  const complete = status === 'COMPLETED' || events.some(event => event.type === 'generation.completed');
  const outlineDone = complete || !!outline || status === 'GENERATING' || status === 'REVIEWING';
  const sourcesDone = outlineDone || !!sources || status === 'PLANNING';
  const sourcesStarted = sourcesDone || status === 'RESEARCHING' ||
    (!!intent && events.some(event => event.id > intent.id && event.type === 'agent.started'));
  const answered = !!question && !!resume && resume.id > question.id;
  const followUp = !!sources && !!question && question.id > sources.id;
  const slidesDone = new Set(events.filter(event => event.type === 'slide.completed').map(event => event.payload.index));

  const tasks = [
    { id: 'request', label: 'Analyze the request', done: sourcesStarted || !!intent || !!question || status === 'WAITING_FOR_USER' },
    { id: 'clarify', label: 'Clarify requirements', done: sourcesStarted || answered },
    { id: 'sources', label: 'Review available sources', done: sourcesDone },
    ...(followUp ? [{ id: 'follow-up', label: 'Review follow-up questions', done: outlineDone || answered || status === 'PLANNING' }] : []),
    { id: 'outline', label: 'Plan the slide outline', done: outlineDone },
    ...(outline ? outline.slides.map((slide, index) => ({
      id: 'slide-' + index, label: `Slide ${index + 1} · ${slide.title}`, done: complete || slidesDone.has(index),
    })) : [{ id: 'slides', label: 'Build and review slides', done: complete }]),
    { id: 'finalize', label: 'Finalize the presentation', done: complete },
  ];
  const stopped = status === 'FAILED' || status === 'CANCELLED';
  const active = stopped || status === 'CREATED' ? undefined : tasks.find(task => !task.done)?.id;
  const state = status === 'FAILED' ? 'Failed' : status === 'CANCELLED' ? 'Stopped' :
    active ? (status === 'WAITING_FOR_USER' && !answered ? 'Waiting for you' : 'In progress') : '';
  return { tasks, active, state, count: tasks.filter(task => task.done).length };
}

@Component({
  selector: 'studio-generation-plan',
  imports: [StudioIcon],
  templateUrl: './generation-plan.html',
  host: { class: 'generation-plan', '[class.plan-waiting]': "status() === 'WAITING_FOR_USER'" },
})
export class GenerationPlan {
  readonly events = input.required<readonly StudioEvent[]>();
  readonly status = input.required<Status>();
  readonly summary = computed(() => generationPlan(this.events(), this.status()));
  readonly expanded = signal(true);
  readonly i = icons;
  private readonly steps = viewChild<ElementRef<HTMLOListElement>>('steps');
  private readonly active = computed(() => this.summary().active);

  constructor() {
    afterRenderEffect(() => {
      const active = this.active();
      const steps = this.steps()?.nativeElement;
      if (!active || !steps) return;
      const row = steps.querySelector('[aria-current="step"]');
      if (!row) return;
      const list = steps.getBoundingClientRect();
      const current = row.getBoundingClientRect();
      if (current.bottom > list.bottom) steps.scrollTop += current.bottom - list.bottom;
      else if (current.top < list.top) steps.scrollTop -= list.top - current.top;
    });
  }
}
