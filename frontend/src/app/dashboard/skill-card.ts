import { Component, input, output } from '@angular/core';
import { Skill } from '../models';
import { icons, StudioIcon } from '../icons';

@Component({
  selector: 'studio-skill-card',
  imports: [StudioIcon],
  template: `
    <article class="skill-card">
      <button class="skill-art" type="button" [class]="'skill-art ' + skill().preview" (click)="preview.emit(skill())" [attr.aria-label]="'Preview ' + skill().name">
        <span class="mini-deck" [style.background]="skill().theme.background" [style.color]="skill().theme.ink">
          <span class="mini-label">{{ skill().category }}</span>
          <strong>{{ skill().name }}</strong>
          <span class="mini-rule" [style.background]="skill().theme.accent"></span>
          <span class="mini-lines"></span>
          <span class="mini-art" [style.--art-ink]="skill().theme.accent"><b></b><b></b><b></b><b></b></span>
        </span>
      </button>
      <div class="skill-info">
        <div class="skill-name">
          <h3>{{ skill().name }}</h3>
          <div class="skill-actions">
            @if (canDelete()) {
              <button class="icon-button delete-icon" type="button" [attr.aria-label]="'Delete ' + skill().name" title="Delete skill"
                [disabled]="busy()" (click)="removed.emit(skill())"><lucide-icon [img]="i.Trash2" [size]="17" /></button>
            }
            <button class="icon-button" type="button" [attr.aria-label]="'Use ' + skill().name" (click)="chosen.emit(skill())"><lucide-icon [img]="i.ArrowUpRight" [size]="17" /></button>
          </div>
        </div>
        <p>{{ skill().description }}</p>
        <div class="skill-tags">@for (tag of skill().tags; track $index) { <span>{{ tag }}</span> }
          <small title="Supported aspect ratios">{{ skill().formats.join(' · ') }}</small>
        </div>
      </div>
    </article>
  `,
})
export class SkillCard {
  skill = input.required<Skill>();
  canDelete = input(false);
  busy = input(false);
  preview = output<Skill>();
  chosen = output<Skill>();
  removed = output<Skill>();
  readonly i = icons;
}
