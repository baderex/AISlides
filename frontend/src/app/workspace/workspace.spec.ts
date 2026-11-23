import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, expect, it, vi } from 'vitest';
import { Api } from '../api';
import { Presentation, Slide, StudioEvent } from '../models';
import { Workspace } from './workspace';

afterEach(() => vi.unstubAllGlobals());

it('opens the outline as empty slides and fills the selected position as generation progresses', async () => {
  const titles = ['Introduction', 'Findings', 'Next steps'];
  const deck: Presentation = {
    id: 'deck', title: 'Report', status: 'PLANNING', revision: 1, created: '', updated: '',
    slide_count: 0, aspect_ratio: '16:9', thumbnail: null,
    request: {prompt: 'Report', aspect_ratio: '16:9', skill_id: null, asset_ids: [], research: false,
      generate_images: false, generate: true},
    document: {schema_version: 1, title: 'Report', aspect_ratio: '16:9', sources: [], slides: [],
      theme: {background: '#ffffff', ink: '#000000', accent: '#333333', muted: '#444444', surface: '#eeeeee', font: 'Arial'}},
  };
  let stream!: MockStream;
  class MockStream {
    onmessage!: (event: MessageEvent) => void;
    constructor() { stream = this; }
    close() {}
  }
  vi.stubGlobal('EventSource', MockStream);
  const api = {deck: async () => structuredClone(deck), url: () => '/events'};
  TestBed.configureTestingModule({providers: [provideRouter([]), {provide: Api, useValue: api}]});
  const fixture = TestBed.createComponent(Workspace);
  const workspace = fixture.componentInstance;
  const event = (id: number, type: string, payload: Partial<StudioEvent['payload']> = {}) => stream.onmessage(new MessageEvent('message', {
    data: JSON.stringify({id, type, payload: {message: type, ...payload}, job_id: 'job', presentation_id: 'deck'}),
  }));
  await workspace.open('deck');
  deck.status = 'GENERATING';
  deck.planned_slide_titles = titles;
  event(1, 'outline.completed');
  await vi.waitFor(() => expect(workspace.slideSlots()).toHaveLength(3));
  fixture.detectChanges();
  const root: HTMLElement = fixture.nativeElement;
  const thumbnail = root.querySelectorAll<HTMLButtonElement>('.thumbnail')[1];
  thumbnail.click();
  fixture.detectChanges();
  expect(root.querySelector('.canvas-frame')?.textContent).toContain('Findings');
  expect(root.querySelector('.canvas-status')?.textContent).toContain('2 / 3');

  const slides: Slide[] = titles.map((title, i) => ({id: String(i), title, layout: 'summary', background: '#ffffff',
    elements: [], source_ids: [], warnings: [], notes: ''}));
  deck.document.slides.push(slides[0]);
  event(2, 'slide.completed');
  await vi.waitFor(() => expect(workspace.visibleSlides()).toHaveLength(1));
  expect(workspace.displayedSlot()?.title).toBe('Findings');
  const draft = {...slides[1], title: 'Findings before review'};
  deck.review_draft = {index: 1, slide: draft, message: 'Reviewing the first draft.'};
  event(3, 'slide.draft', deck.review_draft);
  fixture.detectChanges();
  expect(root.querySelector('.canvas-frame .slide-canvas')?.getAttribute('aria-label')).toBe(draft.title);
  deck.status = 'FAILED';
  deck.slide_update_id = 3;
  deck.review_feedback = 'Correct the chart label.';
  await workspace.open('deck');
  fixture.detectChanges();
  expect(root.querySelectorAll('.thumbnail')).toHaveLength(3);
  expect(workspace.displayedSlide()?.title).toBe(draft.title);
  expect(workspace.canEditSlide()).toBe(false);
  expect(root.querySelector('.slide-warnings')?.textContent).toContain('Correct the chart label.');
  deck.status = 'REVIEWING';
  event(4, 'generation.resumed');
  await vi.waitFor(() => expect(workspace.canEdit()).toBe(false));
  const revised = {...draft, title: 'Corrected findings'};
  event(5, 'slide.draft', {index: 1, slide: revised, message: 'Reviewing the revised draft.'});
  fixture.detectChanges();
  expect(root.querySelector('.canvas-frame .slide-canvas')?.getAttribute('aria-label')).toBe(revised.title);
  workspace.compareDraft();
  fixture.detectChanges();
  expect(root.querySelector('.canvas-frame .slide-canvas')?.getAttribute('aria-label')).toBe(draft.title);
  workspace.compareDraft();
  deck.document.slides.push(slides[1]);
  delete deck.review_draft;
  deck.slide_update_id = 6;
  event(6, 'slide.completed');
  await vi.waitFor(() => expect(workspace.editor.active()).toBe(slides[1].id));
  fixture.detectChanges();
  expect(root.querySelectorAll('.thumbnail')[1].getAttribute('aria-pressed')).toBe('true');
  expect(root.querySelector('.canvas-frame .slide-canvas')?.getAttribute('aria-label')).toBe('Findings');

  deck.document.slides.push(slides[2]);
  deck.status = 'COMPLETED';
  delete deck.planned_slide_titles;
  event(7, 'generation.completed');
  await vi.waitFor(() => expect(workspace.canEdit()).toBe(true));
  expect(workspace.displayedSlide()?.id).toBe(slides[1].id);
  expect(workspace.slideSlots()).toHaveLength(3);
});
