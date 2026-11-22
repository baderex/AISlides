import { DatePipe } from '@angular/common';
import { afterNextRender, Component, computed, ElementRef, inject, Injector, signal, viewChild } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink, RouterLinkActive } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { Api, errorMessage } from '../api';
import { Dialog } from '../dialog';
import { icons, StudioIcon } from '../icons';
import { Asset, CreatePresentation, GenerationCapabilities, LibrarySkill, PresentationSummary, Skill } from '../models';
import { SlideCanvas } from '../workspace/canvas';
import { Composer } from './composer';
import { SkillCard } from './skill-card';

type DeleteTarget = { kind: 'presentation' | 'skill'; id: string; name: string };

@Component({
  selector: 'studio-dashboard',
  imports: [Composer, SkillCard, Dialog, FormsModule, DatePipe, RouterLink, RouterLinkActive, SlideCanvas, StudioIcon],
  templateUrl: './dashboard.html',
})
export class Dashboard {
  readonly api = inject(Api);
  private router = inject(Router);
  private injector = inject(Injector);
  private routeUrl = toSignal(inject(ActivatedRoute).url, { requireSync: true });
  private composer = viewChild(Composer);
  private presentationSection = viewChild<ElementRef<HTMLElement>>('presentationSection');
  private skillSection = viewChild<ElementRef<HTMLElement>>('skillSection');
  readonly i = icons;
  readonly skills = signal<LibrarySkill[]>([]);
  readonly recent = signal<PresentationSummary[]>([]);
  readonly capabilities = signal<GenerationCapabilities | null>(null);
  readonly user = signal<{email: string; local: boolean} | null>(null);
  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly deletedKind = signal<DeleteTarget['kind'] | null>(null);
  readonly authRequired = signal(false);
  readonly allowSignup = signal(false);
  readonly authMode = signal<'login' | 'register'>('login');
  readonly assets = signal<Asset[]>([]);
  readonly selectedSkill = signal<Skill | null>(null);
  readonly preview = signal<Skill | null>(null);
  readonly section = computed(() => this.routeUrl()[0]?.path ?? 'home');
  readonly visiblePresentations = computed(() => this.section() === 'home' ? this.recent().slice(0, 3) : this.recent());
  readonly deleteTarget = signal<DeleteTarget | null>(null);
  readonly search = signal('');
  readonly category = signal('All skills');
  readonly modal = signal<'skill' | 'help' | null>(null);
  readonly skillMode = signal<'scratch' | 'brand' | 'reference' | 'import'>('scratch');
  readonly suggested = [
    { text: 'Quarterly business review', prompt: 'Create an 8-slide quarterly business review for an executive leadership team.' },
    { text: 'Product launch pitch', prompt: 'Create a product launch pitch with a clear audience, value proposition and rollout plan.' },
    { text: 'Explain a complex topic', prompt: 'Generate three slides about quantum computing.' },
  ];
  readonly categories = computed(() => ['All skills', ...new Set(this.skills().map(skill => skill.category))]);
  readonly filteredSkills = computed(() => {
    const term = this.search().trim().toLowerCase();
    const category = this.category();
    return this.skills().filter(skill => (category === 'All skills' || skill.category === category) &&
      (!term || [skill.name, skill.description, skill.category, ...skill.tags].some(text => text.toLowerCase().includes(term))));
  });
  readonly visibleSkills = computed(() => this.section() === 'skills' || this.category() !== 'All skills' || this.search()
    ? this.filteredSkills() : this.filteredSkills().slice(0, 3));
  email = '';
  password = '';
  skillName = '';
  skillCategory = 'Custom';
  skillDescription = '';
  skillInstructions = '';
  skillFile: File | null = null;
  apiSkillLink(id: string) { return this.api.skillLink(id); }

  constructor() { void this.load(); }

  async load() {
    this.loading.set(true);
    this.error.set('');
    this.deletedKind.set(null);
    try {
      this.user.set(await this.api.session());
      this.authRequired.set(false);
      const [decks, skills, capabilities] = await Promise.all([
        this.api.list(), this.api.skills(), this.api.capabilities().catch(() => null),
      ]);
      this.recent.set(decks);
      this.skills.set(skills);
      this.capabilities.set(capabilities);
    } catch (error) {
      if (error instanceof HttpErrorResponse && error.status === 401) {
        this.authRequired.set(true);
        try { this.allowSignup.set((await this.api.authConfig()).allow_signup); } catch { this.allowSignup.set(false); }
      } else this.error.set(errorMessage(error));
    } finally { this.loading.set(false); }
  }

  async signIn() {
    await this.action(async () => {
      await this.api.auth(this.authMode(), this.email, this.password);
      this.password = '';
      await this.load();
    });
  }

  async signOut() {
    await this.action(async () => { await this.api.logout(); this.user.set(null); await this.load(); });
  }

  async start(prompt?: string) {
    if (this.section() === 'home') {
      this.composer()?.start(prompt);
    } else if (await this.router.navigate(['/'])) {
      afterNextRender({ write: () => this.composer()?.start(prompt) }, { injector: this.injector });
    }
  }

  async upload(files: File[]) {
    await this.action(async () => {
      for (const file of files) {
        if (this.assets().length >= 15) throw new Error('You can attach up to 15 references.');
        const asset = await this.api.upload(file);
        this.assets.update(list => [...list, asset]);
      }
    });
  }

  removeAsset(id: string) { this.assets.update(items => items.filter(item => item.id !== id)); }

  choose(skill: Skill) {
    this.selectedSkill.set(skill);
    this.preview.set(null);
    void this.start();
  }

  async generate(request: CreatePresentation) {
    await this.action(async () => {
      const created = await this.api.create(request);
      await this.router.navigate(['/presentation', created.id]);
    });
  }

  showDelete(kind: DeleteTarget['kind'], id: string, name: string) {
    this.error.set('');
    this.deletedKind.set(null);
    this.deleteTarget.set({ kind, id, name });
  }

  closeDelete() {
    if (this.busy()) return;
    this.error.set('');
    this.deleteTarget.set(null);
  }

  async deleteItem(item: DeleteTarget) {
    await this.action(async () => {
      const isSkill = item.kind === 'skill';
      try {
        await (isSkill ? this.api.removeSkill(item.id) : this.api.remove(item.id));
      } catch (error) {
        if (!(error instanceof HttpErrorResponse) || error.status !== 404) throw error;
      }
      if (isSkill) {
        this.skills.update(items => items.filter(skill => skill.id !== item.id));
        if (this.selectedSkill()?.id === item.id) this.selectedSkill.set(null);
        if (!this.categories().includes(this.category())) this.category.set('All skills');
      } else {
        this.recent.update(items => items.filter(presentation => presentation.id !== item.id));
      }
      this.deleteTarget.set(null);
      this.deletedKind.set(item.kind);
      const section = isSkill ? this.skillSection : this.presentationSection;
      afterNextRender({ write: () => section()?.nativeElement.focus() }, { injector: this.injector });
    });
  }

  setFile(event: Event) { this.skillFile = (event.target as HTMLInputElement).files?.[0] ?? null; }

  async addSkill() {
    await this.action(async () => {
      let skill: Skill;
      if (this.skillMode() === 'scratch') {
        skill = await this.api.addSkill({
          schema_version: 1, name: this.skillName.trim(), category: this.skillCategory.trim(),
          description: this.skillDescription.trim(), instructions: this.skillInstructions.trim(), tags: [],
          formats: ['16:9', '4:3', '1:1'], audience: '', recipes: ['cover', 'summary', 'closing'], preview: 'strategy',
          theme: { background: '#EEF1FF', ink: '#191C43', accent: '#4F50D5', muted: '#4B5471', surface: '#DCE1FF', font: 'Arial' },
        });
      } else {
        if (!this.skillFile) throw new Error('Choose a file to continue.');
        const asset = await this.api.upload(this.skillFile);
        skill = await this.api.skillFromAsset(asset.id, this.skillMode() === 'brand');
      }
      this.skills.update(items => [...items, { ...skill, can_delete: true }]);
      this.modal.set(null);
      this.skillName = this.skillDescription = this.skillInstructions = '';
      this.skillFile = null;
      this.choose(skill);
    });
  }

  private async action(callback: () => Promise<void>) {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    try { await callback(); }
    catch (error) { this.error.set(error instanceof HttpErrorResponse ? errorMessage(error) : error instanceof Error ? error.message : errorMessage(error)); }
    finally { this.busy.set(false); }
  }
}
