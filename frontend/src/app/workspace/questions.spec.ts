import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';
import { Questions } from './questions';

describe('Clarification cards', () => {
  it('advances and submits the explicit answer with a default for unanswered options', async () => {
    const fixture = TestBed.createComponent(Questions);
    fixture.componentRef.setInput('questions', [
      { id: 'audience', type: 'audience', title: 'Audience', description: '', required: true, allow_custom_answer: false,
        options: [{id: 'board', label: 'Board', description: '', swatch: null}, {id: 'students', label: 'Students', description: '', swatch: null}],
        recommended_option_id: 'board' },
      { id: 'count', type: 'slide_count', title: 'Slide count', description: '', required: true, allow_custom_answer: true, recommended_count: 3 },
    ]);
    fixture.detectChanges();
    const emit = vi.fn();
    fixture.componentInstance.submitted.subscribe(emit);
    const element = fixture.nativeElement as HTMLElement;
    element.querySelector<HTMLInputElement>('input[value="students"]')?.click();
    fixture.detectChanges();
    Array.from(element.querySelectorAll('button')).find(button => button.textContent?.trim() === 'Continue')?.click();
    fixture.detectChanges();
    expect(element.textContent).toContain('Question 2 of 2');
    element.querySelector<HTMLButtonElement>('button.defaults')?.click();
    expect(emit).toHaveBeenCalledWith([
      {question_id: 'audience', option_ids: ['students'], text: '', use_default: false},
      {question_id: 'count', option_ids: [], text: '', use_default: true},
    ]);
  });
});
