import { Component, computed, effect, input, output, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { Asset, Aspect, CreatePresentation, Mode, ProviderSettings, Skill } from '../models';
import { icons, StudioIcon } from '../icons';
import { ProviderLabel } from './provider-label';

@Component({
  selector: 'studio-composer',
  imports: [ReactiveFormsModule, StudioIcon, ProviderLabel],
  templateUrl: './composer.html',
})
export class Composer {
  config = input<ProviderSettings | null>(null);
  skill = input<Skill | null>(null);
  assets = input<Asset[]>([]);
  busy = input(false);
  generating = output<CreatePresentation>();
  upload = output<File[]>();
  remove = output<string>();
  clearSkill = output();
  readonly i = icons;
  readonly prompt = new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.minLength(3)] });
  readonly provider = new FormControl('', { nonNullable: true });
  readonly aspect = new FormControl<Aspect>('16:9', { nonNullable: true });
  readonly model = new FormControl('', { nonNullable: true });
  readonly mode = signal<Mode>('professional');
  readonly guide = signal(false);
  readonly research = signal(false);
  readonly images = signal(false);
  readonly advanced = signal(false);
  readonly formats = computed<Aspect[]>(() => this.skill()?.formats ?? ['16:9', '4:3', '1:1']);

  constructor() {
    effect(() => {
      const formats = this.formats();
      if (!formats.includes(this.aspect.value)) this.aspect.setValue(formats[0]);
    });
  }

  files(event: Event) {
    const target = event.target as HTMLInputElement;
    this.upload.emit(Array.from(target.files ?? []));
    target.value = '';
  }

  submit() {
    if (this.prompt.invalid || this.busy() || !this.prompt.value.trim()) return;
    this.generating.emit({ prompt: this.prompt.value.trim(), provider: this.provider.value || this.config()?.default || null,
      model: this.model.value.trim() || null, aspect_ratio: this.formats().includes(this.aspect.value) ? this.aspect.value : this.formats()[0],
      mode: this.guide() ? 'guided' : this.mode(), skill_id: this.skill()?.id ?? null, asset_ids: this.assets().map(asset => asset.id),
      research: this.research(), generate_images: this.images(), generate: true });
  }

  start(prompt?: string) {
    if (prompt) this.prompt.setValue(prompt);
    document.getElementById('presentation-prompt')?.focus();
  }
}
