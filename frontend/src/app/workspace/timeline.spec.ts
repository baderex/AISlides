import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';
import { StudioEvent } from '../models';
import { Timeline } from './timeline';

function event(id: number, type: string, message: string, payload: Partial<StudioEvent['payload']> = {}): StudioEvent {
  return {
    id, type, timestamp: '2026-01-01T12:00:00Z', job_id: 'job', presentation_id: 'deck',
    payload: { message, ...payload },
  };
}

describe('Agent timeline', () => {
  it('keeps slide warnings and run failures visible when the routine activity is collapsed', () => {
    const fixture = TestBed.createComponent(Timeline);
    fixture.componentRef.setInput('events', [
      event(1, 'slide.started', 'Designing slide 2.'),
      event(2, 'slide.completed', 'Slide 2 is ready.', { index: 1, warnings: ['Check text contrast.'] }),
      event(3, 'generation.failed', 'The provider was unavailable.'),
    ]);
    fixture.detectChanges();

    const root: HTMLElement = fixture.nativeElement;
    const thinking = root.querySelector('details')!;
    expect(thinking.open).toBe(false);
    expect(thinking.querySelectorAll('li')).toHaveLength(2);
    expect(thinking.textContent).not.toContain('Check text contrast.');
    expect(root.querySelector('.agent-review')?.textContent).toContain('Slide 2 review notes');
    expect(root.querySelector('.agent-review')?.textContent).toContain('Check text contrast.');
    expect(root.querySelector('.event-failed')?.textContent).toContain('The provider was unavailable.');
  });

  it('keeps an expanded Thinking section open as new events arrive', () => {
    const fixture = TestBed.createComponent(Timeline);
    const first = event(1, 'agent.started', 'Analyzing the request.');
    fixture.componentRef.setInput('events', [first]);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    root.querySelector('details')!.open = true;

    fixture.componentRef.setInput('events', [first, event(2, 'agent.progress', 'Topic identified.')]);
    fixture.detectChanges();

    expect(root.querySelector('details')?.open).toBe(true);
    expect(root.querySelector('details summary')?.textContent).toContain('2 updates');
    expect(root.querySelectorAll('details li')).toHaveLength(2);
  });
});
