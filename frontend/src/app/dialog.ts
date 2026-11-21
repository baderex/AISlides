import { CdkTrapFocus } from '@angular/cdk/a11y';
import { Component, HostListener, input, output } from '@angular/core';
import { icons, StudioIcon } from './icons';

@Component({
  selector: 'studio-dialog',
  imports: [CdkTrapFocus, StudioIcon],
  template: `
    <div class="dialog-backdrop" (click)="closed.emit()">
      <section role="dialog" aria-modal="true" aria-labelledby="dialog-title" class="dialog"
               cdkTrapFocus [cdkTrapFocusAutoCapture]="true" (click)="$event.stopPropagation()">
        <div class="dialog-heading"><h2 id="dialog-title">{{ title() }}</h2>
          <button type="button" class="icon-button" aria-label="Close" (click)="closed.emit()"><lucide-icon [img]="icon" /></button>
        </div>
        <ng-content />
      </section>
    </div>
  `,
})
export class Dialog {
  title = input.required<string>();
  closed = output();
  icon = icons.X;

  @HostListener('document:keydown.escape')
  onEscape() { this.closed.emit(); }
}
