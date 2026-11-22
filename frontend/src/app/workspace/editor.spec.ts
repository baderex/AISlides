import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';
import { Deck } from '../models';
import { Editor } from './editor';

const deck: Deck = {
  title: 'Test deck', schema_version: 1, aspect_ratio: '16:9', sources: [],
  theme: {background: '#FFFFFF', ink: '#181D3B', accent: '#4C52D9', muted: '#555555', surface: '#F5F5F5', font: 'Arial'},
  slides: [{id: 'intro', title: 'Intro', layout: 'cover', background: '#FFFFFF', elements: [], notes: '', source_ids: [], warnings: []}],
};

describe('Editor commands', () => {
  it('applies, undoes and redoes a durable IR edit', () => {
    TestBed.configureTestingModule({providers: [Editor]});
    const editor = TestBed.inject(Editor);
    editor.accept(structuredClone(deck), true);
    const text = editor.addText();
    editor.updateElement(text.id, {text: 'Edited heading'});
    expect(editor.selectedText()?.text).toBe('Edited heading');
    editor.undo();
    expect(editor.selectedText()?.text).toBe('New text');
    editor.redo();
    expect(editor.selectedText()?.text).toBe('Edited heading');
    expect(editor.dirty()).toBe(true);
    expect(deck.slides[0].elements).toHaveLength(0);
  });

  it('downgrades the visible evidence status immediately when a value label is edited', () => {
    TestBed.configureTestingModule({providers: [Editor]});
    const editor = TestBed.inject(Editor);
    editor.accept(structuredClone(deck), true);
    const value = editor.addText();
    const badge = editor.addText();
    editor.updateElement(badge.id, {element_key: 'exhibit:evidence', text: 'Exhibit data: source verified'});
    editor.updateElement(value.id, {text: '12'});
    expect(editor.selectedText()?.text).toBe('Exhibit data: unverified');
    editor.undo();
    expect(editor.selectedText()?.text).toBe('Exhibit data: source verified');
  });
});
