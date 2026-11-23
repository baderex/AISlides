import { NgTemplateOutlet } from '@angular/common';
import { Component, computed, input, output, signal } from '@angular/core';
import { Aspect, ChartElement, ConnectorAnchor, Geometry, LineElement, Slide, SlideElement, TableElement, TextElement } from '../models';
import { FitText } from './fit-text';

type Point = [number, number];
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
  artwork = input<string | null>(null);
  editable = input(false);
  selected = input<string | null>(null);
  snap = input(true);
  selection = output<string | null>();
  transform = output<{id: string; change: Change}>();
  textEdit = output<string>();
  readonly draft = signal<{id: string; change: Change} | null>(null);
  readonly ratio = computed(() => this.aspect().replace(':', '/'));
  polygonPoints(points: [number, number][]) { return points.map(([x, y]) => `${x * 100},${y * 100}`).join(' '); }
  private readonly widthPoints = computed(() => ({'16:9': 960, '4:3': 720, '1:1': 540})[this.aspect()]);
  private drag: Drag | null = null;
  readonly shades = ['#574ADB', '#8B72DA', '#279990', '#BF693F', '#637B92'];

  geometry(element: SlideElement): Change {
    const draft = this.draft();
    return draft?.id === element.id ? draft.change : element;
  }

  connectedLine(element: LineElement) {
    const anchorPoint = (anchor?: ConnectorAnchor | null): Point | null => {
      const node = anchor && this.slide().elements.find(el => el.id === anchor.shape_id && el.type === 'shape');
      if (!node || !anchor) return null;
      const {x, y, w, h} = this.geometry(node);
      const [dx, dy] = {top: [.5, 0], left: [0, .5], bottom: [.5, 1], right: [1, .5]}[anchor.side];
      return [x + w * dx, y + h * dy];
    };
    const anchoredStart = anchorPoint(element.start_anchor);
    const anchoredEnd = anchorPoint(element.end_anchor);
    if (!anchoredStart && !anchoredEnd && element.route !== 'elbow' &&
        (!element.arrows || element.arrows === 'none') && element.weight <= 2) return null;
    const {x, y, w, h} = this.geometry(element);
    const [from, to] = {
      diagonal: [[x, y], [x + w, y + h]],
      'diagonal-up': [[x, y + h], [x + w, y]],
      'diagonal-down-left': [[x + w, y], [x, y + h]],
      'diagonal-up-left': [[x + w, y + h], [x, y]],
      right: [[x, y + h / 2], [x + w, y + h / 2]],
      left: [[x + w, y + h / 2], [x, y + h / 2]],
      down: [[x + w / 2, y], [x + w / 2, y + h]],
      up: [[x + w / 2, y + h], [x + w / 2, y]],
    }[element.direction ?? 'diagonal'] as [Point, Point];
    const a = anchoredStart ?? from, b = anchoredEnd ?? to;
    const middleX = (a[0] + b[0]) / 2;
    const points: Point[] = element.route === 'elbow' && a[0] !== b[0] && a[1] !== b[1]
      ? [a, [middleX, a[1]], [middleX, b[1]], b] : [a, b];
    const left = Math.max(0, Math.min(a[0], b[0]) - .015);
    const top = Math.max(0, Math.min(a[1], b[1]) - .022);
    const width = Math.min(1, Math.max(a[0], b[0]) + .015) - left;
    const height = Math.min(1, Math.max(a[1], b[1]) + .022) - top;
    const coords = ([px, py]: Point) => [(px - left) / width * 100, (py - top) / height * 100];
    const arrow = (start: Point, end: Point) => {
      const dx = (end[0] - start[0]) * this.widthPoints();
      const dy = (end[1] - start[1]) * 540;
      const length = Math.hypot(dx, dy);
      if (length < 1) return '';
      const depth = Math.min(length / 2, Math.max(6, element.weight * 3.5));
      const baseX = end[0] - dx / length * depth / this.widthPoints();
      const baseY = end[1] - dy / length * depth / 540;
      const sideX = dy / length * depth * .4 / this.widthPoints();
      const sideY = dx / length * depth * .4 / 540;
      return [coords(end), coords([baseX - sideX, baseY + sideY]), coords([baseX + sideX, baseY - sideY])]
        .map(point => point.join(',')).join(' ');
    };
    return {
      geometry: {x: left, y: top, w: width, h: height},
      path: 'M ' + points.map(point => coords(point).join(' ')).join(' L '),
      startTip: element.arrows === 'both' ? arrow(points[1], a) : '',
      endTip: element.arrows === 'both' || element.arrows === 'end' ? arrow(points[points.length - 2], b) : '',
    };
  }

  textRuns(element: TextElement) {
    if (!element.spans?.length) return [{text: element.text, color: element.color, bold: element.bold}];
    const chars = Array.from(element.text);
    const runs: {text: string; color: string; bold: boolean}[] = [];
    let cursor = 0;
    for (const span of element.spans) {
      if (span.start > cursor) {
        runs.push({text: chars.slice(cursor, span.start).join(''), color: element.color, bold: element.bold});
      }
      runs.push({
        text: chars.slice(span.start, span.end).join(''),
        color: span.color ?? element.color,
        bold: span.bold ?? element.bold,
      });
      cursor = span.end;
    }
    if (cursor < chars.length) {
      runs.push({text: chars.slice(cursor).join(''), color: element.color, bold: element.bold});
    }
    return runs;
  }

  font(size: number) { return size / this.widthPoints() * 100 + 'cqw'; }
  fontFamily(name?: string) { return JSON.stringify(name || 'Arial'); }

  linePath(direction?: string) {
    return ({
      right: 'M 0 50 H 100', left: 'M 100 50 H 0',
      down: 'M 50 0 V 100', up: 'M 50 100 V 0',
      'diagonal-up': 'M 0 100 L 100 0',
      'diagonal-down-left': 'M 100 0 L 0 100',
      'diagonal-up-left': 'M 100 100 L 0 0',
    } as Record<string, string>)[direction ?? ''] || 'M 0 0 L 100 100';
  }

  tableFormat(table: TableElement, width: number, height: number) {
    const points = width * this.widthPoints();
    const compact = !!table.row_rule && points < 360;
    const count = table.rows[0].length;
    const numericCount = table.numeric_columns?.length ?? 0;
    const mixed = !!table.row_rule && count >= 4 && numericCount > 0 && numericCount < count;
    const weights = table.css?.columns ?? table.rows[0].map((_, index) => (compact || mixed) && !table.numeric_columns?.includes(index) ? 1.5 : 1);
    const total = weights.reduce((sum, weight) => sum + weight, 0);
    return {
      insetY: this.font(table.css ? 3 : Math.min(6, (height * 540 / table.rows.length) * .15)),
      columns: weights.map(weight => ({
        percent: 100 * weight / total,
        insetX: this.font(table.css ? 6 : Math.min(12, points * weight / total * .1)),
      })),
    };
  }

  tableNegative(table: TableElement, row: number, column: number, cell: string) {
    return !!table.negative_ink && row > 0 && table.numeric_columns?.includes(column)
      && /^[-−]\s*[$€£¥]?\s*(?:\d[\d,]*(?:\.\d+)?|\.\d+)\s*%?$/.test(cell.trim());
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
    if (target.type === 'line' && (target.start_anchor || target.end_anchor)) return;
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

  needsChartKey(el: ChartElement) {
    return el.element_key === 'workbook:chart' || el.chart === 'pie' || !el.show_values ||
      el.series.length > 1 && el.chart !== 'stacked_column';
  }

  chart(el: ChartElement) {
    const values = el.series.flatMap(series => series.values);
    const min = Math.min(...values), max = Math.max(...values);
    let low = Math.min(0, min), high = Math.max(0, max);
    const labeledBars = el.show_values && (el.chart === 'bar' || el.chart === 'column');
    const focusedLine = el.chart === 'line' && el.highlight != null;
    if (focusedLine) {
      const span = max - min || Math.abs(max) * .1 || 1;
      const magnitude = 10 ** Math.floor(Math.log10(span / 2));
      const factor = [1, 2, 5, 10].find(value => value * magnitude >= span / 2) ?? 10;
      const step = factor * magnitude;
      low = Math.floor((min - .45 * span) / step) * step;
      high = Math.ceil((max + .65 * span) / step) * step;
    } else if (labeledBars) {
      const padding = .22 * (high - low || 1);
      if (high > 0 || low === 0) high += padding;
      if (low < 0) low -= padding;
    }
    const span = high - low || 1;
    const band = 100 / el.categories.length;
    const layout = el.composition;
    const plotHeight = layout ? 100 * layout.plot_height / layout.height : el.chart === 'bar' ? 96 : 87;
    const baseline = plotHeight * high / span;
    return el.series.map(series => series.values.map((value, index) => {
      const current = plotHeight * (high - value) / span;
      const lineY = el.chart === 'line' && el.show_values && el.highlight == null ? plotHeight * .13 + current * .82 : current;
      const thickness = .76 * band / el.series.length;
      const horizontal = plotHeight / el.categories.length * .72 / el.series.length;
      const inset = el.chart === 'bar' ? (layout ? 100 * layout.bar_inset / layout.plot_width : 24) : 0;
      return { x: band*(index+.12), y: Math.min(current, baseline), w: thickness*.85, h: Math.abs(current-baseline),
        cx: band*(index+.5), cy: lineY, horizontal, bx: inset + (100 - inset) * (Math.min(0, value)-low)/span,
        by: (index + .14) * plotHeight / el.categories.length + .1 * horizontal,
        bw: Math.abs(value)*(100 - inset)/span, negative: value < 0 };
    }));
  }

  chartPointValue(el: ChartElement, index: number, series = 0) {
    const number = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
    const value = el.series[series].values[index];
    return (value < 0 ? '−' : '') + (el.value_prefix ?? '') + number.format(Math.abs(value)) + (el.value_suffix ?? '');
  }

  segmentInk(color: string) {
    const channels = [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16) / 255)
      .map(channel => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4);
    return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2] <= .1833 ? '#FFFFFF' : '#000000';
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
    if (el.composition) return el.composition.axis_labels;
    if (el.chart !== 'stacked_column') return el.categories;
    const number = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
    return this.chartTotals(el).map((total, i) => `${el.categories[i]}  ${el.value_prefix ?? ''}${number.format(total)}${el.value_suffix ?? ''}`);
  }

  private chartTotals(el: ChartElement) {
    return el.categories.map((_, i) => el.series.reduce((sum, series) => sum + series.values[i], 0));
  }

  chartColor(el: ChartElement, index: number) {
    const palette = el.palette?.length ? el.palette : [el.color, ...this.shades.slice(1),
      ...(el.chart === 'pie' ? ['#A33766', '#927500', '#345B29', '#542657', '#206688', '#A84728'] : [])];
    return palette[index % palette.length];
  }

  chartMarkColor(el: ChartElement, index: number, series: number) {
    return el.highlight != null && el.highlight !== index && el.context_color
      ? el.context_color : this.chartColor(el, series);
  }

  pieSlices(el: ChartElement) {
    const total = el.series[0].values.reduce((sum, value) => sum + value, 0) || 1;
    let angle = -Math.PI / 2;
    return el.series[0].values.map(value => {
      const start = angle;
      angle += value / total * 2 * Math.PI;
      const mid = (start + angle) / 2;
      const point = (a: number) => [50 + 45 * Math.cos(a), 50 + 45 * Math.sin(a)].join(' ');
      // Two arcs also describe a full circle when there is just one category.
      const path = 'M50 50 L' + point(start) + ' A45 45 0 0 1 ' + point(mid) + ' A45 45 0 0 1 ' + point(angle) + ' Z';
      return {path, value, x: 50 + 31.5 * Math.cos(mid), y: 50 + 31.5 * Math.sin(mid)};
    });
  }

  keyTracks(values: number[], unit: number) {
    return values.map(value => value / unit * 100 + '%').join(' ');
  }
}
