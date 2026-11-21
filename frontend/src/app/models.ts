export type Aspect = '16:9' | '4:3' | '1:1';
export type Mode = 'professional' | 'creative' | 'guided';
export type Status = 'CREATED' | 'ANALYZING' | 'WAITING_FOR_USER' | 'RESEARCHING' | 'PLANNING' | 'GENERATING' | 'REVIEWING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';
export type Layout = 'cover' | 'section' | 'summary' | 'comparison' | 'timeline' | 'process' | 'metrics' | 'chart' | 'showcase' | 'editorial' | 'research' | 'quote' | 'closing';

export interface Theme { background: string; ink: string; accent: string; muted: string; surface: string; font: string; profile?: 'standard' | 'consulting' }
export interface Source { id: string; title: string; url: string | null; excerpt: string; asset_id: string | null }
export interface Geometry {
  id: string; x: number; y: number; w: number; h: number; rotation: number; z: number;
  opacity: number; border: string | null; edited: boolean;
  placeholder_idx?: number | null;
}
export interface TextElement extends Geometry {
  type: 'text'; text: string; size: number; color: string; font: string; bold: boolean; align: 'left' | 'center' | 'right';
}
export interface ShapeElement extends Geometry { type: 'shape'; shape: 'rect' | 'round' | 'ellipse' | 'triangle'; fill: string }
export interface ImageElement extends Geometry { type: 'image'; asset_id: string; alt: string }
export interface LineElement extends Geometry { type: 'line'; color: string; weight: number }
export interface TableElement extends Geometry { type: 'table'; rows: string[][]; color: string; fill: string; size?: number; header_fill?: string | null; header_color?: string | null }
export interface ChartElement extends Geometry {
  type: 'chart'; chart: 'bar' | 'column' | 'line' | 'pie'; categories: string[]; series: {name: string; values: number[]}[]; color: string;
}
export interface GroupElement extends Geometry { type: 'group'; elements: SlideElement[] }
export type SlideElement = TextElement | ShapeElement | ImageElement | LineElement | TableElement | ChartElement | GroupElement;
export interface Slide { id: string; title: string; layout: Layout; background: string; elements: SlideElement[]; notes: string; source_ids: string[]; warnings: string[] }
export interface Deck { schema_version: 1; title: string; aspect_ratio: Aspect; theme: Theme; sources: Source[]; slides: Slide[] }
export interface PresentationSummary {
  id: string; title: string; status: Status; revision: number; created: string; updated: string; slide_count: number;
  aspect_ratio: Aspect; thumbnail: Slide | null;
}
export interface CreatePresentation {
  prompt: string; mode: Mode; aspect_ratio: Aspect; provider: string | null;
  model: string | null; skill_id: string | null; asset_ids: string[]; research: boolean; generate_images: boolean; generate: boolean;
}
export interface Presentation extends PresentationSummary { document: Deck; request: CreatePresentation }
export interface Skill {
  id: string; schema_version: 1; name: string; category: string; description: string; tags: string[]; formats: Aspect[];
  audience: string; instructions: string; recipes: Layout[]; theme: Theme; preview: 'strategy' | 'pitch' | 'research' | 'product' | 'data' | 'education';
  brand?: {layouts: {index: number; name: string}[]} | null;
}
export interface ExportReview {
  id: string;
  revision: number;
  slides: {number: number; title: string; image: string; issues: {code: string; message: string}[]}[];
}
export interface Provider { id: string; label: string; model: string; configured: boolean; demo: boolean; vision: boolean }
export interface ProviderSettings { providers: Provider[]; default: string; research: boolean; images: boolean }
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
