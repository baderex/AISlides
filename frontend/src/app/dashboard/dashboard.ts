import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { Api, errorMessage } from '../api';
import { Dialog } from '../dialog';
import { icons, StudioIcon } from '../icons';
import { Asset, CreatePresentation, PresentationSummary, Provider, ProviderSettings, Skill } from '../models';
import { SlideCanvas } from '../workspace/canvas';
import { Composer } from './composer';
import { SkillCard } from './skill-card';

@Component({
  selector: 'studio-dashboard',
  imports: [Composer, SkillCard, Dialog, FormsModule, DatePipe, RouterLink, SlideCanvas, StudioIcon],
  templateUrl: './dashboard.html',
})
export class Dashboard {
  private api = inject(Api);
  private router = inject(Router);
  private composer = viewChild(Composer);
  readonly i = icons;
  readonly skills = signal<Skill[]>([]);
  readonly recent = signal<PresentationSummary[]>([]);
  readonly config = signal<ProviderSettings | null>(null);
  readonly user = signal<{email: string; local: boolean} | null>(null);
  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly authRequired = signal(false);
  readonly allowSignup = signal(false);
  readonly authMode = signal<'login' | 'register'>('login');
  readonly assets = signal<Asset[]>([]);
  readonly selectedSkill = signal<Skill | null>(null);
  readonly preview = signal<Skill | null>(null);
  readonly section = signal('home');
  readonly search = signal('');
  readonly category = signal('All skills');
  readonly showAll = signal(false);
  readonly modal = signal<'settings' | 'skill' | 'help' | null>(null);
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
  readonly visibleSkills = computed(() => this.showAll() || this.category() !== 'All skills' || this.search()
    ? this.filteredSkills() : this.filteredSkills().slice(0, 3));
  email = '';
  password = '';
  skillName = '';
  skillCategory = 'Custom';
  skillDescription = '';
  skillInstructions = '';
  skillFile: File | null = null;
  isDemo(provider: Provider) { return provider.demo; }
  apiSkillLink(id: string) { return this.api.skillLink(id); }

  constructor() { void this.load(); }

  async load() {
    this.loading.set(true);
    this.error.set('');
    try {
      this.user.set(await this.api.session());
      this.authRequired.set(false);
      const [decks, skills, config] = await Promise.all([this.api.list(), this.api.skills(), this.api.providers()]);
      this.recent.set(decks);
      this.skills.set(skills);
      this.config.set(config);
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

  go(section: string) {
    this.section.set(section);
    if (section === 'skills') this.showAll.set(true);
    document.getElementById(section)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  start(prompt?: string) { this.go('home'); this.composer()?.start(prompt); }

  demo() {
    this.modal.set(null);
    this.composer()?.provider.setValue('demo');
    this.start(this.suggested[2].prompt);
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
    this.start();
  }

  async generate(request: CreatePresentation) {
    await this.action(async () => {
      const created = await this.api.create(request);
      await this.router.navigate(['/presentation', created.id]);
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
      this.skills.update(items => [...items, skill]);
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
