import { afterRenderEffect, DestroyRef, Directive, ElementRef, inject, input } from '@angular/core';
import { TableElement, TextElement } from '../models';

@Directive({ selector: '[studioFitText]' })
export class FitText {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly destroy = inject(DestroyRef);
  studioFitText = input.required<TextElement | TableElement>();
  preferredFont = input.required<string>();

  constructor() {
    afterRenderEffect(() => {
      this.studioFitText();
      this.fit();
    });
  }

  ngAfterViewInit() {
    if (typeof ResizeObserver === 'undefined' || !this.host.parentElement) return;
    const observer = new ResizeObserver(() => this.fit());
    observer.observe(this.host.parentElement);
    this.destroy.onDestroy(() => observer.disconnect());
  }

  private fit() {
    const frame = this.host.parentElement;
    if (!frame?.clientWidth || !frame.clientHeight) return;
    this.host.style.fontSize = this.preferredFont();
    const overflows = () => this.host.scrollHeight > frame.clientHeight || this.host.scrollWidth > frame.clientWidth;
    if (!overflows()) return;

    let high = Number.parseFloat(getComputedStyle(this.host).fontSize);
    let low = high * 8 / (this.studioFitText().size ?? 12);
    if (!Number.isFinite(high) || low >= high) return;
    for (let step = 0; step < 8; step++) {
      const trial = (low + high) / 2;
      this.host.style.fontSize = trial + 'px';
      if (overflows()) high = trial;
      else low = trial;
    }
    this.host.style.fontSize = low + 'px';
  }
}
