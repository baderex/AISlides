import { Component, OnInit, input, output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ExhibitRecipe, ExhibitSpec, ExhibitVisual } from '../models';

export interface ExhibitChange {
  visual: ExhibitVisual;
  recipe: ExhibitRecipe;
  illustrative: boolean;
}

@Component({
  selector: 'studio-exhibit-editor',
  imports: [FormsModule],
  templateUrl: './exhibit-editor.html',
  styles: [`
    .values { display: grid; gap: 8px; margin: 0; }
    .values label { display: grid; gap: 4px; min-width: 0; font-size: 13px; }
    .values .field { min-width: 0; width: 100%; }
    .values fieldset { padding: 8px; margin: 0; border: 1px solid currentColor; border-radius: 6px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 8px; }
    .evidence { padding-inline-start: 18px; font-size: 13px; }
    .check { display: flex; align-items: center; gap: 8px; font-size: 14px; }
  `],
})
export class ExhibitEditor implements OnInit {
  readonly spec = input.required<ExhibitSpec>();
  readonly busy = input(false);
  readonly error = input('');
  readonly apply = output<ExhibitChange>();
  readonly recipeNames: Record<ExhibitRecipe, string> = {
    standard: 'Standard', exhibit_callouts: 'With callout',
    exhibit_horizontal: 'Horizontal groups', exhibit_milestones: 'Milestone focus',
  };
  visual!: ExhibitVisual;
  recipe!: ExhibitRecipe;
  illustrative = false;
  readonly model = {standalone: true};

  ngOnInit() {
    this.visual = structuredClone(this.spec().content.visual!);
    this.recipe = this.spec().recipe;
    this.illustrative = this.spec().content.illustrative_exhibit ?? false;
  }

  labelWithUnit(label?: string, unit?: string | null) {
    return unit ? `${label} (${unit})` : label;
  }

  addRelationship() {
    const {nodes, edges} = this.visual;
    if (!edges || !nodes || nodes.length < 2 || edges.length >= 12) return;
    edges.push({source: nodes[0].id, target: nodes[1].id, label: '',
      weight: this.visual.type === 'sankey' ? 1 : null});
  }
}
