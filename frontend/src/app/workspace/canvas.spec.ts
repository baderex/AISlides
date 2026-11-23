import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';
import { ChartElement, Slide } from '../models';
import { SlideCanvas } from './canvas';

describe('Workbook chart labels', () => {
  it('updates the visible series, categories and amounts when chart data changes', () => {
    const fixture = TestBed.createComponent(SlideCanvas);
    const chart: ChartElement = {
      id: 'chart', type: 'chart', chart: 'pie', element_key: 'workbook:chart',
      x: 0, y: 0, w: 1, h: 1, rotation: 0, z: 0, opacity: 1, edited: false, border: null,
      color: '#915c00', categories: ['Enterprise', 'Consumer', 'Government'],
      series: [{name: 'Revenue', values: [50, 30, 20]}], value_suffix: 'm',
    };
    const slide: Slide = {id: 'slide', title: 'Accounts', layout: 'chart', background: '#ffffff',
      elements: [chart], notes: '', source_ids: [], warnings: []};
    fixture.componentRef.setInput('slide', slide);
    fixture.detectChanges();
    const key = () => fixture.nativeElement.querySelector('.chart-key').textContent;
    for (const text of ['Revenue', 'Enterprise', 'Consumer', 'Government', '50m', '30m', '20m']) {
      expect(key()).toContain(text);
    }
    fixture.componentRef.setInput('slide', {...slide, elements: [{...chart, chart: 'bar', series: [
      ...chart.series, {name: 'Cost', values: [-12, 24, 9]},
    ]}]});
    fixture.detectChanges();
    expect(key()).toContain('Cost');
    expect(key()).toContain('−12m');
    expect(fixture.nativeElement.querySelectorAll('.chart-bar-category')).toHaveLength(3);
  });
});
