import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, input, output, signal } from '@angular/core';
import { Aspect, ChartElement, Geometry, Slide, SlideElement, TableElement } from '../models';
import { FitText } from './fit-text';

type Change = Pick<Geometry, 'x' | 'y' | 'w' | 'h'>;
interface Drag { id: string; pointer: number; startX: number; startY: number; resize: boolean; original: Change }

function bounded(value: number, min: number, max: number) { return Math.min(max, Math.max(min, value)); }

@Component({
  selector: 'studio-canvas',
  imports: [NgTemplateOutlet, FitText],
  templateUrl: './canvas.html',
})
export class SlideCanvas {
  slide = input.required<Slide>();
  aspect = input<Aspect>('16:9');
  editable = input(false);
  selected = input<string | null>(null);
  snap = input(true);
  selection = output<string | null>();
  transform = output<{id: string; change: Change}>();
  textEdit = output<string>();
  readonly draft = signal<{id: string; change: Change} | null>(null);
  readonly ratio = computed(() => this.aspect().replace(':', '/'));
  private readonly widthPoints = computed(() => ({'16:9': 960, '4:3': 720, '1:1': 540})[this.aspect()]);
  private drag: Drag | null = null;
  readonly shades = ['#574ADB', '#8B72DA', '#279990', '#BF693F', '#637B92'];

  geometry(element: SlideElement): Change {
    const draft = this.draft();
    return draft?.id === element.id ? draft.change : element;
  }

  font(size: number) { return size / this.widthPoints() * 100 + 'cqw'; }

  tableFormat(table: TableElement, width: number, height: number) {
    const points = width * this.widthPoints();
    const compact = !!table.row_rule && points < 360;
    const weights = table.rows[0].map((_, index) => compact && !table.numeric_columns?.includes(index) ? 1.4 : 1);
    const total = weights.reduce((sum, weight) => sum + weight, 0);
    return {
      insetY: this.font(Math.min(6, (height * 540 / table.rows.length) * .15)),
      columns: weights.map(weight => ({
        percent: 100 * weight / total,
        insetX: this.font(Math.min(12, points * weight / total * .1)),
      })),
    };
  }

  radius(geometry: Change) {
    const heightToWidth = {'16:9': .5625, '4:3': .75, '1:1': 1}[this.aspect()];
    return Math.min(geometry.w, geometry.h * heightToWidth) * 12 + 'cqw';
  }

  pointerStart(event: PointerEvent, id: string, resize: boolean, canvas: HTMLElement) {
    if (!this.editable() || event.button !== 0) return;
    const target = this.slide().elements.find(element => element.id === id);
    if (!target) return;
    event.stopPropagation();
    event.preventDefault();
    canvas.focus({preventScroll: true});
    this.selection.emit(id);
    this.drag = { id, resize, pointer: event.pointerId, startX: event.clientX, startY: event.clientY, original: target };
    canvas.setPointerCapture(event.pointerId);
  }

  pointerMove(event: PointerEvent, canvas: HTMLElement) {
    const drag = this.drag;
    if (!drag || drag.pointer !== event.pointerId) return;
    const dx = (event.clientX - drag.startX) / canvas.clientWidth;
    const dy = (event.clientY - drag.startY) / canvas.clientHeight;
    const {x, y, w, h} = drag.original;
    if (Math.abs(dx) + Math.abs(dy) < .003 && !this.draft()) return;
    const snap = (value: number) => this.snap() && !event.altKey ? Math.round(value * 100) / 100 : value;
    const align = (value: number) => Math.abs(value - .5) < .011 ? .5 : snap(value);
    const change = drag.resize
      ? {x, y, w: bounded(snap(w + dx), .025, 1-x), h: bounded(snap(h + dy), .025, 1-y)}
      : {x: bounded(align(x + dx + w / 2) - w / 2, 0, 1-w), y: bounded(align(y + dy + h / 2) - h / 2, 0, 1-h), w, h};
    this.draft.set({ id: drag.id, change });
  }

  pointerEnd(event: PointerEvent, apply: boolean) {
    if (this.drag?.pointer !== event.pointerId) return;
    const draft = this.draft();
    if (apply && draft) this.transform.emit(draft);
    this.draft.set(null);
    this.drag = null;
  }

  aligned(axis: 'x' | 'y') {
    const draft = this.draft();
    return !!draft && Math.abs((axis === 'x' ? draft.change.x + draft.change.w/2 : draft.change.y + draft.change.h/2) - .5) < .001;
  }

  chart(el: ChartElement) {
    const values = el.series.flatMap(series => series.values);
    const high = Math.max(0, ...values), low = Math.min(0, ...values), span = high - low || 1;
    const band = 100 / el.categories.length;
    const baseline = 85 * high / span;
    return el.series.map(series => series.values.map((value, index) => {
      const current = 85 * (high - value) / span;
      const thickness = .76 * band / el.series.length;
      const horizontal = 85 / el.categories.length * .72 / el.series.length;
      return { x: band*(index+.12), y: Math.min(current, baseline), w: thickness*.85, h: Math.abs(current-baseline),
        cx: band*(index+.5), cy: current, horizontal, bx: 100 * (Math.min(0, value)-low)/span,
        by: index * 85 / el.categories.length, bw: Math.abs(value)*100/span };
    }));
  }

  readonly stackedBaseline = 97;

  stackedColumns(el: ChartElement) {
    const totals = this.chartTotals(el);
    const scale = this.stackedBaseline * .95 / (Math.max(...totals) || 1);
    const band = 100 / el.categories.length;
    const remaining = el.categories.map(() => this.stackedBaseline);
    return el.series.map(series => series.values.map((value, index) => {
      const height = value * scale;
      remaining[index] -= height;
      return { x: band * (index + .26), y: remaining[index], width: band * .48, height };
    }));
  }

  chartLabels(el: ChartElement) {
    if (el.chart !== 'stacked_column') return el.categories;
    const number = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
    return this.chartTotals(el).map((total, i) => `${el.categories[i]}  ${el.value_prefix ?? ''}${number.format(total)}`);
  }

  private chartTotals(el: ChartElement) {
    return el.categories.map((_, i) => el.series.reduce((sum, series) => sum + series.values[i], 0));
  }

  chartColor(el: ChartElement, index: number) {
    const palette = el.palette?.length ? el.palette : [el.color, ...this.shades.slice(1)];
    return palette[index % palette.length];
  }

  pie(el: ChartElement) {
    const total = el.series[0].values.reduce((sum, value) => sum + value, 0) || 1;
    let start = 0;
    return 'conic-gradient(' + el.series[0].values.map((value, index) => {
      const end = start + value * 100 / total;
      const entry = this.chartColor(el, index) + ' ' + start + '% ' + end + '%';
      start = end;
      return entry;
    }).join(',') + (el.series[0].values.length === 1 ? ', transparent 100%' : '') + ')';
  }
}
