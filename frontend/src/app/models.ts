export type Aspect = '16:9' | '4:3' | '1:1';
export type Status = 'CREATED' | 'ANALYZING' | 'WAITING_FOR_USER' | 'RESEARCHING' | 'PLANNING' | 'GENERATING' | 'REVIEWING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';
export type Layout = 'cover' | 'section' | 'summary' | 'comparison' | 'timeline' | 'process' | 'metrics' | 'chart' | 'showcase' | 'editorial' | 'research' | 'quote' | 'closing';

export interface DesignRules {
  family?: 'corporate' | 'consulting' | 'editorial' | 'technology' | 'financial' | 'product';
  audience: string; purpose: string; industry: string; mood: string; brand_constraints: string; visual_references: string;
  imagery: string; heading_font: string; spacing: 'compact' | 'regular' | 'spacious'; card_shape: 'rect' | 'round';
  cover_style: 'rules' | 'panel' | 'circles'; cover_background: string; dense_surface: string; dense_ink: string; chart_colors: string[];
}
export interface Theme { design?: DesignRules | null; background: string; ink: string; accent: string; muted: string; surface: string; font: string; profile?: 'standard' | 'consulting' }
export interface Source { id: string; title: string; url: string | null; excerpt: string; asset_id: string | null }
export interface Geometry {
  id: string; x: number; y: number; w: number; h: number; rotation: number; z: number;
  element_key?: string | null;
  opacity: number; border: string | null; edited: boolean;
  placeholder_idx?: number | null;
}
export interface TextSpan { start: number; end: number; color?: string | null; bold?: boolean | null }
export interface TextElement extends Geometry {
  role?: 'cover' | 'headline' | 'body' | 'label' | 'code' | 'footnote' | null;
  minimum_pt?: number | null;
  single_line?: boolean; line_spacing?: number | null;
  spans?: TextSpan[];
  type: 'text'; text: string; size: number; color: string; font: string; bold: boolean; align: 'left' | 'center' | 'right';
}
export interface ShapeElement extends Geometry { type: 'shape'; radius_pt?: number | null; shape: 'rect' | 'round' | 'ellipse' | 'triangle' | 'diamond'; fill: string; polygon?: [number, number][] | null }
export interface ImageElement extends Geometry { type: 'image'; fit?: 'fill' | 'contain' | 'cover'; focal_x?: number; focal_y?: number; asset_id: string; alt: string }
export interface ConnectorAnchor { shape_id: string; side: 'top' | 'left' | 'bottom' | 'right' }
export interface LineElement extends Geometry {
  type: 'line'; color: string; weight: number;
  direction?: 'diagonal' | 'diagonal-up' | 'diagonal-down-left' | 'diagonal-up-left' | 'right' | 'left' | 'down' | 'up';
  arrows?: 'none' | 'end' | 'both';
  start_anchor?: ConnectorAnchor | null;
  end_anchor?: ConnectorAnchor | null;
  route?: 'straight' | 'elbow';
  dashed?: boolean;
}
export interface TableElement extends Geometry {
  minimum_pt?: number | null;
  css?: {columns: number[]; rows: number[]; align: ('left' | 'right' | 'center')[][]; bold: boolean[][]} | null;
  type: 'table'; font?: string; rows: string[][]; color: string; fill: string; size?: number;
  header_fill?: string | null; header_color?: string | null; row_rule?: string | null;
  header_rule?: string | null; numeric_columns?: number[];
  focus_rows?: number[]; focus_fill?: string | null; negative_ink?: string | null;
  focus_columns?: number[]; focus_cell_fill?: string | null; focus_ink?: string | null;
  cell_fills?: (string | null)[][]; cell_inks?: (string | null)[][];
}
export interface ChartElement extends Geometry {
  composition?: {width: number; height: number; plot_width: number; plot_height: number; bar_inset: number;
    axis_labels: string[]; axis_size: number; numbered: boolean; key_size: number; key_left: number;
    key_top: number; columns: number[]; rows: number[]} | null;
  type: 'chart'; font?: string; label_color?: string; palette?: string[]; value_prefix?: string; value_suffix?: string; show_values?: boolean;
  highlight?: number | null; context_color?: string | null;
  chart: 'bar' | 'column' | 'line' | 'pie' | 'stacked_column'; categories: string[]; series: {name: string; values: number[]}[]; color: string;
}
export interface GroupElement extends Geometry { type: 'group'; elements: SlideElement[] }
export type SlideElement = TextElement | ShapeElement | ImageElement | LineElement | TableElement | ChartElement | GroupElement;
export type ExhibitRecipe = 'standard' | 'exhibit_callouts' | 'exhibit_horizontal' | 'exhibit_milestones';
export interface ExhibitVisual {
  type: string;
  baseline?: number | null;
  start_label?: string;
  series?: string[];
  columns?: string[];
  column_units?: string[];
  weights?: number[];
  recommended?: string | null;
  rows?: {label: string; values?: number[]; cells?: (number | string)[]; width?: number | null}[];
  horizontal_label?: string;
  vertical_label?: string;
  size_label?: string;
  horizontal_unit?: string | null;
  vertical_unit?: string | null;
  size_unit?: string | null;
  horizontal_split?: number | null;
  vertical_split?: number | null;
  points?: {label: string; horizontal?: number | null; vertical?: number | null; size?: number | null; quadrant?: string | null}[];
  nodes?: {id: string; label: string}[];
  edges?: {source: string; target: string; label: string; weight?: number | null}[];
  periods?: string[];
  boundaries?: number[];
  items?: {id: string; label: string; start: number; end: number; predecessors: string[]}[];
}
export interface ExhibitBinding {
  path: string; label: string; value: string; status: 'verified' | 'illustrative' | 'derived' | 'unverified';
  source_observation_id?: string | null; calculation?: string | null; inputs: string[];
}
export interface ExhibitSpec {
  recipe: ExhibitRecipe;
  available_recipes?: ExhibitRecipe[];
  value_bindings?: ExhibitBinding[];
  content: {visual?: ExhibitVisual | null; illustrative_exhibit?: boolean};
}
export interface Slide {
  id: string; title: string; layout: Layout; background: string; elements: SlideElement[]; notes: string;
  source_ids: string[]; warnings: string[]; brand_layout_index?: number | null; spec?: ExhibitSpec | null;
}
export interface Deck { schema_version: 1; title: string; aspect_ratio: Aspect; theme: Theme; sources: Source[]; slides: Slide[] }
export interface PresentationSummary {
  id: string; title: string; status: Status; revision: number; created: string; updated: string; slide_count: number;
  aspect_ratio: Aspect; thumbnail: Slide | null;
}
export interface CreatePresentation {
  prompt: string; aspect_ratio: Aspect;
  skill_id: string | null; asset_ids: string[]; research: boolean | null; generate_images: boolean; generate: boolean;
}
export interface Presentation extends PresentationSummary { document: Deck; request: CreatePresentation }
export interface Skill {
  id: string; schema_version: 1; name: string; category: string; description: string; tags: string[]; formats: Aspect[];
  audience: string; instructions: string; recipes: Layout[]; theme: Theme; preview: 'strategy' | 'pitch' | 'research' | 'product' | 'data' | 'education';
  brand?: {layouts: {index: number; name: string}[]} | null;
}
export interface LibrarySkill extends Skill { can_delete: boolean }
export interface ExportReview {
  id: string;
  revision: number;
  slides: {number: number; title: string; image: string; issues: {code: string; message: string}[]}[];
}
export interface GenerationCapabilities { research: boolean; images: boolean }
export interface Asset { id: string; name: string; media_type: string; size: number }
export interface Version { revision: number; label: string; created: string }
export interface StudioEvent {
  id: number; type: string; timestamp: string; job_id: string | null; presentation_id: string;
  payload: { message: string; slide_id?: string; index?: number; outline?: {title: string; slides: { title: string }[]}; sources?: Source[]; uncertainties?: string[]; warnings?: string[] };
}
interface QuestionBase { id: string; title: string; description: string; required: boolean; allow_custom_answer: boolean }
export interface Option { id: string; label: string; description: string; swatch: string | null }
export interface Choice extends QuestionBase {
  type: 'single_select' | 'multi_select' | 'visual_style' | 'theme' | 'audience' | 'yes_no' | 'objective' | 'detail_level' | 'output_format';
  options: Option[]; recommended_option_id: string | null;
}
export interface FreeText extends QuestionBase { type: 'free_text'; recommended_text: string | null }
export interface SlideCount extends QuestionBase { type: 'slide_count'; recommended_count: number }
export type Question = Choice | FreeText | SlideCount;
export interface Answer { question_id: string; option_ids: string[]; text: string; use_default: boolean }
export interface QuestionBatch { waiting: boolean; questions: Question[]; answers: (Answer & {value: string})[] }
export interface EditPreview { revision: number; document: Deck; proposal: {summary: string; operations: unknown[]} }
