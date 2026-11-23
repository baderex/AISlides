import { DatePipe } from '@angular/common';
import { Component, HostListener, OnDestroy, computed, inject, signal, viewChild, ElementRef } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Api, errorMessage } from '../api';
import { Dialog } from '../dialog';
import { icons, StudioIcon } from '../icons';
import { Answer, EditPreview, ExportReview, Presentation, Question, Slide, StudioEvent, Version } from '../models';
import { SlideCanvas } from './canvas';
import { Editor, baseGeometry } from './editor';
import { ExhibitChange, ExhibitEditor } from './exhibit-editor';
import { GenerationPlan } from './generation-plan';
import { Questions } from './questions';
import { Timeline } from './timeline';

const STATUS_LABEL: Record<Presentation['status'], string> = {
  CREATED: 'Queued', ANALYZING: 'Analyzing request', WAITING_FOR_USER: 'Awaiting answers', RESEARCHING: 'Reviewing sources',
  PLANNING: 'Planning slides', GENERATING: 'Designing slides', REVIEWING: 'Reviewing layout', COMPLETED: 'Ready', FAILED: 'Failed', CANCELLED: 'Cancelled',
};

@Component({
  selector: 'studio-workspace',
  imports: [RouterLink, SlideCanvas, Questions, Timeline, GenerationPlan, Dialog, FormsModule, DatePipe, StudioIcon, ExhibitEditor],
  providers: [Editor],
  templateUrl: './workspace.html',
})
export class Workspace implements OnDestroy {
  private api = inject(Api);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  readonly editor = inject(Editor);
  readonly i = icons;
  readonly item = signal<Presentation | null>(null);
  readonly events = signal<StudioEvent[]>([]);
  readonly questions = signal<Question[]>([]);
  readonly versions = signal<Version[]>([]);
  readonly preview = signal<EditPreview | null>(null);
  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly exporting = signal(false);
  readonly exportReview = signal<ExportReview | null>(null);
  readonly reviewPage = signal(0);
  readonly reviewedSlide = computed(() => this.exportReview()?.slides[this.reviewPage()]);
  readonly error = signal('');
  readonly notice = signal('');
  readonly streaming = signal(false);
  readonly modal = signal<'export' | 'export-review' | 'versions' | 'sources' | 'text' | 'exhibit' | 'menu' | 'delete' | null>(null);
  readonly presenting = signal(false);
  readonly zoom = signal(100);
  readonly snap = signal(true);
  readonly splitWidth = signal(374);
  readonly showAgent = signal(true);
  readonly activeIndex = computed(() => this.editor.document()?.slides.findIndex(slide => slide.id === this.editor.current()?.id) ?? -1);
  readonly statusLabel = computed(() => {
    const item = this.item();
    if (!item) return 'Loading';
    return item.status === 'CREATED' && this.canEdit() ? 'Draft' : STATUS_LABEL[item.status];
  });
  readonly canEdit = computed(() => ['COMPLETED', 'FAILED', 'CANCELLED'].includes(this.item()?.status ?? '') || this.item()?.request.generate === false && this.item()?.status === 'CREATED');
  readonly showPlan = computed(() => this.item() !== null && (!this.canEdit() || this.events().some(event => event.job_id !== null)));
  readonly sourceLinks = computed(() => this.editor.document()?.sources.filter(source => this.editor.current()?.source_ids.includes(source.id)) ?? []);
  readonly exhibit = computed(() => {
    const spec = this.editor.current()?.spec;
    const visual = spec?.content.visual;
    return visual ? spec : null;
  });
  readonly displayedSlide = computed(() => this.preview()?.document.slides.find(slide => slide.id === this.editor.current()?.id) ?? this.editor.current());
  readonly canvasArea = viewChild<ElementRef<HTMLElement>>('canvasArea');
  readonly agentScroll = viewChild<ElementRef<HTMLElement>>('agentScroll');
  private stream: EventSource | null = null;
  private id = '';
  private sequence = 0;
  private refreshTimer?: ReturnType<typeof setTimeout>;
  private splitStart: { x: number; width: number } | null = null;
  private dragSlide: string | null = null;
  private textId: string | null = null;
  instruction = '';
  chatScope: 'slide' | 'deck' = 'slide';
  textDraft = '';
  private routeSubscription = this.route.paramMap.subscribe(params => { const id = params.get('id'); if (id) void this.open(id); });

  artwork(slide: Slide) {
    const item = this.item();
    return item?.request.skill_id ? this.api.artwork(item.id, slide) : null;
  }

  ngOnDestroy() {
    this.routeSubscription.unsubscribe();
    this.stream?.close();
    clearTimeout(this.refreshTimer);
    this.sequence++;
  }

  async open(id: string) {
    this.id = id;
    this.stream?.close();
    clearTimeout(this.refreshTimer);
    this.item.set(null);
    this.events.set([]);
    this.questions.set([]);
    this.preview.set(null);
    this.editor.active.set(null);
    this.editor.selected.set(null);
    this.editor.document.set(null);
    this.editor.dirty.set(false);
    this.loading.set(true);
    this.streaming.set(false);
    this.error.set('');
    const okay = await this.refresh(id, true);
    if (!okay || id !== this.id) return;
    this.loading.set(false);
    const stream = new EventSource(this.api.url(id, '/events'));
    this.stream = stream;
    stream.onopen = () => { if (this.stream === stream) this.streaming.set(true); };
    stream.onerror = () => { if (this.stream === stream) this.streaming.set(false); };
    stream.onmessage = message => {
      if (this.stream !== stream) return;
      const event = JSON.parse(message.data) as StudioEvent;
      if (event.id <= (this.events().at(-1)?.id ?? 0)) return;
      this.events.update(items => [...items, event]);
      if (['generation', 'question', 'slide', 'agent'].some(prefix => event.type.startsWith(prefix))) this.scheduleRefresh();
      this.scrollAgent(false);
    };
  }

  private scheduleRefresh() {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => { void this.refresh(); }, 130);
  }

  private async refresh(id = this.id, reset = false): Promise<boolean> {
    const sequence = ++this.sequence;
    try {
      const current = await this.api.deck(id);
      const batch = current.status === 'WAITING_FOR_USER' ? await this.api.questions(id) : null;
      if (this.sequence !== sequence || id !== this.id) return false;
      if (!this.editor.dirty()) this.editor.accept(current.document, reset);
      this.item.set(this.editor.dirty() && this.item() ? {...current, revision: this.item()!.revision} : current);
      const questions = batch?.waiting ? batch.questions : [];
      const openingQuestions = questions.length > 0 && this.questions().length === 0;
      this.questions.set(questions);
      if (openingQuestions) this.scrollAgent(true);
      return true;
    } catch (error) {
      if (this.sequence === sequence && id === this.id) {
        this.error.set(errorMessage(error));
        this.loading.set(false);
      }
      return false;
    }
  }

  private scrollAgent(force: boolean) {
    const area = this.agentScroll()?.nativeElement;
    if (!force && (this.questions().length || !area || area.scrollHeight - area.scrollTop - area.clientHeight >= 160)) return;
    setTimeout(() => {
      const current = this.agentScroll()?.nativeElement;
      if (!current) return;
      const question = current.querySelector('.question-card');
      if (question) {
        current.scrollTop += question.getBoundingClientRect().top - current.getBoundingClientRect().top - 8;
      } else {
        current.scrollTop = current.scrollHeight;
      }
    }, 80);
  }

  async sendAnswers(answers: Answer[]) {
    await this.action(async () => { await this.api.answer(this.id, answers); this.questions.set([]); await this.refresh(); });
  }

  async retry() { await this.action(async () => { await this.api.generate(this.id); await this.refresh(); }); }
  async cancel() { await this.action(async () => { await this.api.cancel(this.id); await this.refresh(); }); }

  async save() { await this.action(async () => { await this.persist(); this.notice.set('Presentation saved.'); }); }

  openExhibit() {
    this.error.set('');
    this.modal.set('exhibit');
  }

  async updateExhibit(change: ExhibitChange) {
    await this.action(async () => {
      const item = this.item(), slide = this.editor.current(), id = this.id;
      if (!item || !slide) return;
      const result = await this.api.editExhibit(id, item.revision, slide, change.visual, change.recipe, change.illustrative);
      if (id !== this.id || this.modal() !== 'exhibit') return;
      if (this.editor.current() !== slide) throw new Error('The slide changed. Reopen the exhibit and try again.');
      this.editor.updateSlide(next => Object.assign(next, result));
      this.modal.set(null);
    });
  }

  private async persist() {
    if (!this.editor.dirty()) return;
    const current = this.item(), document = this.editor.document();
    if (!current || !document) return;
    const saved = await this.api.save(this.id, current.revision, document, 'Manual edit');
    this.sequence++;
    this.item.set(saved);
    // Keep edits made while the save was in flight.
    if (this.editor.document() === document) this.editor.accept(saved.document);
  }

  async versionsOpen() {
    this.modal.set('versions');
    await this.action(async () => this.versions.set(await this.api.versions(this.id)));
  }

  async restore(version: Version) {
    await this.action(async () => {
      if (this.editor.dirty()) throw new Error('Save current changes before restoring a version.');
      const item = this.item();
      if (!item) return;
      const saved = await this.api.restore(this.id, version.revision, item.revision);
      this.editor.accept(saved.document, true);
      this.item.set(saved);
      this.modal.set(null);
      this.notice.set('Version restored.');
    });
  }

  async duplicate() {
    await this.action(async () => {
      await this.persist();
      const created = await this.api.duplicate(this.id);
      this.modal.set(null);
      await this.router.navigate(['/presentation', created.id]);
    });
  }

  async remove() {
    await this.action(async () => { await this.api.remove(this.id); this.modal.set(null); await this.router.navigate(['/']); });
  }

  async saveSkill() {
    await this.action(async () => { await this.persist(); await this.api.saveSkill(this.id); this.modal.set(null); this.notice.set('Skill saved to the library.'); });
  }

  async chat() {
    const text = this.instruction.trim();
    if (text.length < 3 || !this.canEdit()) return;
    await this.action(async () => {
      await this.persist();
      this.preview.set(await this.api.chat(this.id, text, this.chatScope === 'slide' ? this.editor.current()?.id : undefined));
      this.instruction = '';
    });
  }

  async applyPreview() {
    await this.action(async () => {
      const proposal = this.preview();
      const before = this.editor.document();
      if (!proposal || !before) return;
      const updated = await this.api.apply(this.id, proposal);
      this.editor.history.update(items => [...items.slice(-39), before]);
      this.editor.future.set([]);
      this.editor.accept(updated.document);
      this.item.set(updated);
      this.preview.set(null);
      this.notice.set('Proposed edits applied.');
    });
  }

  async export(format: 'pptx' | 'pdf') {
    if (this.exporting() || this.busy()) return;
    this.exporting.set(true);
    await this.action(async () => {
      await this.persist();
      const response = await this.api.export(this.id, format);
      if (!response.body) throw new Error('The export was empty.');
      const link = document.createElement('a');
      const url = URL.createObjectURL(response.body);
      link.href = url;
      link.download = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] ?? ('presentation.' + format);
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      this.modal.set(null);
      this.notice.set(format.toUpperCase() + ' exported.');
    });
    this.exporting.set(false);
  }

  async reviewPowerPoint() {
    if (this.exporting() || this.busy()) return;
    const id = this.id;
    this.exportReview.set(null);
    this.exporting.set(true);
    await this.action(async () => {
      await this.persist();
      const revision = this.item()?.revision;
      if (id !== this.id || revision === undefined) return;
      const review = await this.api.reviewExport(id, revision);
      if (id !== this.id || this.modal() !== 'export') return;
      this.exportReview.set(review);
      const firstFinding = review.slides.findIndex(slide => slide.issues.length > 0);
      this.reviewPage.set(Math.max(0, firstFinding));
      this.modal.set('export-review');
    });
    this.exporting.set(false);
  }

  reviewedDownload(id: string) { return this.api.url(this.id, '/exports/' + id); }

  addText() { if (this.canEdit()) this.openText(this.editor.addText().id); }
  addShape() {
    if (!this.canEdit()) return;
    this.editor.addElement({...baseGeometry(), type: 'shape', shape: 'rect', fill: this.editor.document()?.theme.accent ?? '#4C52D9'});
  }

  async addImage(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file || !this.canEdit()) return;
    await this.action(async () => {
      const asset = await this.api.upload(file);
      this.editor.addElement({...baseGeometry(), type: 'image', asset_id: asset.id, alt: asset.name, w: .33, h: .37});
    });
  }

  openText(id: string) {
    const text = this.editor.current()?.elements.find(el => el.id === id);
    if (text?.type !== 'text' || !this.canEdit()) return;
    this.textId = id;
    this.textDraft = text.text;
    this.modal.set('text');
  }

  updateText() {
    if (this.textId) {
      this.editor.updateElement(this.textId, {text: this.textDraft});
      this.modal.set(null);
    }
  }

  zoomBy(delta: number) { this.zoom.update(value => Math.max(25, Math.min(200, value + delta))); }
  slidePreview(id: string) { return this.api.url(this.id, '/slides/' + id + '/preview'); }
  editBackground(value: string) { this.editor.updateSlide(slide => { slide.background = value; }); }
  fontSize(id: string, size: number) { if (Number.isFinite(size)) this.editor.updateElement(id, {size: Math.max(8, Math.min(100, size))}); }
  moveCurrent(direction: number) {
    const slide = this.editor.current();
    if (slide) this.editor.moveSlide(slide.id, this.activeIndex() + direction);
  }

  async fullscreen() {
    const area = this.canvasArea()?.nativeElement;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (area?.requestFullscreen) await area.requestFullscreen();
    } catch { this.notice.set('Fullscreen is unavailable in this window.'); }
  }

  stepSlide(step: number) {
    const next = this.editor.document()?.slides[this.activeIndex() + step];
    if (next) this.editor.activate(next.id);
  }

  setDragSlide(id: string) { this.dragSlide = id; }
  dropSlide(event: DragEvent, target: number) {
    event.preventDefault();
    if (this.dragSlide && this.canEdit()) this.editor.moveSlide(this.dragSlide, target);
    this.dragSlide = null;
  }

  splitDown(event: PointerEvent) {
    this.splitStart = {x: event.clientX, width: this.splitWidth()};
    (event.target as HTMLElement).setPointerCapture(event.pointerId);
  }

  splitMove(event: PointerEvent) {
    if (this.splitStart) this.resizeSplit(this.splitStart.width + event.clientX - this.splitStart.x);
  }

  resizeSplit(width: number) { this.splitWidth.set(Math.max(270, Math.min(550, width))); }
  splitUp() { this.splitStart = null; }

  @HostListener('document:keydown', ['$event'])
  shortcut(event: KeyboardEvent) {
    if (this.presenting()) {
      if (event.key === 'Escape') this.presenting.set(false);
      if (event.key === 'ArrowLeft') this.stepSlide(-1);
      if (event.key === 'ArrowRight' || event.key === ' ') this.stepSlide(1);
      return;
    }
    const target = event.target as HTMLElement | null;
    if (this.modal() || this.preview() || target?.matches('input, textarea, select, [contenteditable]') || !this.canEdit()) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      if (event.shiftKey) this.editor.redo(); else this.editor.undo();
    }
    const selected = this.editor.element();
    if (!selected || !target?.closest('.slide-canvas')) return;
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      this.editor.removeElement();
    }
    const offsets: Record<string, [number, number]> = {ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0]};
    const offset = offsets[event.key];
    if (offset) {
      event.preventDefault();
      const amount = event.shiftKey ? .04 : .01;
      this.editor.updateElement(selected.id, {x: Math.max(0, Math.min(1-selected.w, selected.x+offset[0]*amount)),
        y: Math.max(0, Math.min(1-selected.h, selected.y+offset[1]*amount))});
    }
  }

  private async action(callback: () => void | Promise<void>) {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.notice.set('');
    try { await callback(); }
    catch (error) {
      if (error instanceof Error && !('status' in error)) this.error.set(error.message);
      else if (error && typeof error === 'object' && 'error' in error && error.error instanceof Blob) {
        try { this.error.set(JSON.parse(await error.error.text()).detail || errorMessage(error)); }
        catch { this.error.set(errorMessage(error)); }
      } else this.error.set(errorMessage(error));
    }
    finally { this.busy.set(false); }
  }
}
