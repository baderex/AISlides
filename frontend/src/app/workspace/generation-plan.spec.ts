import { describe, expect, it } from 'vitest';
import { StudioEvent } from '../models';
import { generationPlan } from './generation-plan';

function event(id: number, type: string, payload: Partial<StudioEvent['payload']> = {}): StudioEvent {
  return { id, type, timestamp: '', job_id: 'job', presentation_id: 'deck', payload: { message: '', ...payload } };
}

describe('Generation plan', () => {
  it('keeps slide tasks open until the corresponding slide is complete', () => {
    const outline = { title: 'Deck', slides: [{ title: 'Context' }, { title: 'Next steps' }] };
    const events = [
      event(1, 'agent.progress'), event(2, 'research.completed'), event(3, 'outline.completed', { outline }),
      event(4, 'slide.completed', { index: 0 }), event(5, 'slide.started', { index: 1 }),
    ];

    const reviewing = generationPlan(events, 'REVIEWING');
    expect(reviewing.active).toBe('slide-1');
    expect(reviewing.tasks.at(-3)).toEqual({ id: 'slide-0', label: 'Slide 1 · Context', done: true });
    expect(reviewing.tasks.at(-2)?.done).toBe(false);
    expect(reviewing.tasks.at(-1)?.done).toBe(false);

    const finalizing = generationPlan([...events, event(6, 'slide.completed', { index: 1 })], 'GENERATING');
    expect(finalizing.active).toBe('finalize');
    expect(finalizing.count).toBe(finalizing.tasks.length - 1);
  });

  it('shows a follow-up separately after reviewing sources and holds unfinished tasks after failure', () => {
    const events = [
      event(1, 'agent.progress'), event(2, 'question.requested'), event(3, 'generation.resumed'),
      event(4, 'research.completed'), event(5, 'question.requested'),
    ];
    const waiting = generationPlan(events, 'WAITING_FOR_USER');
    expect(waiting.count).toBe(3);
    expect(waiting.active).toBe('follow-up');
    expect(waiting.state).toBe('Waiting for you');

    const failed = generationPlan([...events, event(6, 'generation.resumed')], 'FAILED');
    expect(failed.count).toBe(4);
    expect(failed.active).toBeUndefined();
    expect(failed.state).toBe('Failed');
  });
});
