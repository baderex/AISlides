import { computed, Injectable, signal } from '@angular/core';
import { Deck, Geometry, Slide, SlideElement, TextElement } from '../models';

type ElementUpdate = Partial<Geometry> & {text?: string; size?: number; color?: string; bold?: boolean; align?: 'left' | 'center' | 'right'; fill?: string; shape?: 'rect' | 'round' | 'ellipse' | 'triangle'};

export function slideId() { return crypto.randomUUID(); }
export function baseGeometry(z = 0): Geometry {
  return { id: slideId(), x: .1, y: .1, w: .35, h: .15, z, rotation: 0, opacity: 1, border: null, edited: true };
}

@Injectable()
export class Editor {
  readonly document = signal<Deck | null>(null);
  readonly active = signal<string | null>(null);
  readonly selected = signal<string | null>(null);
  readonly dirty = signal(false);
  readonly history = signal<Deck[]>([]);
  readonly future = signal<Deck[]>([]);
  readonly current = computed(() => this.document()?.slides.find(slide => slide.id === this.active()) ?? this.document()?.slides[0] ?? null);
  readonly element = computed(() => this.current()?.elements.find(el => el.id === this.selected()) ?? null);
  readonly selectedText = computed(() => { const el = this.element(); return el?.type === 'text' ? el : null; });

  accept(next: Deck, reset = false) {
    this.document.set(next);
    if (reset) {
      this.history.set([]);
      this.future.set([]);
    }
    this.dirty.set(false);
    this.reconcile(next);
  }

  activate(id: string) { this.active.set(id); this.selected.set(null); }

  change(change: (next: Deck) => void) {
    const old = this.document();
    if (!old) return;
    const next = structuredClone(old);
    change(next);
    if (JSON.stringify(old) === JSON.stringify(next)) return;
    this.history.update(history => [...history.slice(-39), old]);
    this.future.set([]);
    this.document.set(next);
    this.dirty.set(true);
    this.reconcile(next);
  }

  undo() {
    const previous = this.history().at(-1), current = this.document();
    if (!previous || !current) return;
    this.future.update(items => [current, ...items]);
    this.history.update(items => items.slice(0, -1));
    this.document.set(previous);
    this.dirty.set(true);
    this.reconcile(previous);
  }

  redo() {
    const [next, ...future] = this.future(), current = this.document();
    if (!next || !current) return;
    this.history.update(items => [...items, current]);
    this.future.set(future);
    this.document.set(next);
    this.dirty.set(true);
    this.reconcile(next);
  }

  updateSlide(change: (slide: Slide) => void) {
    const id = this.current()?.id;
    if (id) this.change(next => { const slide = next.slides.find(entry => entry.id === id); if (slide) change(slide); });
  }

  updateElement(id: string, change: ElementUpdate) {
    this.updateSlide(slide => { const element = slide.elements.find(el => el.id === id); if (element) Object.assign(element, change, {edited: true}); });
  }

  removeElement() {
    const id = this.selected();
    if (id) this.updateSlide(slide => { slide.elements = slide.elements.filter(element => element.id !== id); });
  }

  addElement(element: SlideElement) {
    if ((this.current()?.elements.length ?? 0) >= 100) return;
    const top = Math.max(-1, ...(this.current()?.elements.map(el => el.z) ?? []));
    element.z = Math.min(1000, top + 1);
    this.updateSlide(slide => slide.elements.push(element));
    this.selected.set(element.id);
  }

  addText(): TextElement {
    const theme = this.document()?.theme;
    const el: TextElement = { ...baseGeometry(), type: 'text', text: 'New text', size: 26,
      color: theme?.ink ?? '#181D3B', font: theme?.font ?? 'Arial', bold: false, align: 'left' };
    this.addElement(el);
    return el;
  }

  reorderElement(direction: 1 | -1) {
    const id = this.selected();
    if (!id) return;
    this.updateSlide(slide => {
      const items = [...slide.elements].sort((a, b) => a.z - b.z);
      const index = items.findIndex(el => el.id === id);
      if (index < 0 || index + direction < 0 || index + direction >= items.length) return;
      [items[index], items[index+direction]] = [items[index+direction], items[index]];
      items.forEach((el, layer) => el.z = layer);
      slide.elements = items;
    });
  }

  addSlide(duplicate = false) {
    const original = this.current();
    const deck = this.document();
    if (!deck || deck.slides.length >= 40) return;
    const slide: Slide = duplicate && original ? {
      ...structuredClone(original), id: slideId(), title: original.title.slice(0, 210) + ' copy',
    } : { id: slideId(), title: 'Untitled slide', background: deck.theme.background, layout: 'summary', elements: [], source_ids: [], notes: '', warnings: [] };
    this.change(next => {
      const index = original ? next.slides.findIndex(entry => entry.id === original.id) : next.slides.length - 1;
      next.slides.splice(index + 1, 0, slide);
    });
    this.activate(slide.id);
  }

  removeSlide() {
    const active = this.current();
    if (active) this.change(next => { next.slides = next.slides.filter(slide => slide.id !== active.id); });
  }

  moveSlide(id: string, target: number) {
    this.change(next => {
      const index = next.slides.findIndex(slide => slide.id === id);
      if (index < 0 || target < 0 || target >= next.slides.length || index === target) return;
      const [slide] = next.slides.splice(index, 1);
      next.slides.splice(target, 0, slide);
    });
  }

  private reconcile(deck: Deck) {
    if (!deck.slides.some(slide => slide.id === this.active())) this.active.set(deck.slides[0]?.id ?? null);
    if (!this.current()?.elements.some(el => el.id === this.selected())) this.selected.set(null);
  }
}
