import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { Answer, Asset, CreatePresentation, Deck, EditPreview, ExhibitRecipe, ExhibitVisual, ExportReview, GenerationCapabilities, LibrarySkill, Presentation, PresentationSummary, QuestionBatch, Skill, Slide, Version } from './models';

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);
  private root = '/api/v1';
  url(id: string, segment = '') { return this.root + '/presentations/' + id + segment; }
  artwork(id: string, slide: Slide) {
    const query = new URLSearchParams({layout: slide.layout, background: slide.background});
    if (slide.brand_layout_index != null) query.set('index', String(slide.brand_layout_index));
    return this.url(id, '/master-artwork?' + query);
  }
  session() { return firstValueFrom(this.http.get<{email: string; local: boolean}>(this.root + '/session')); }
  authConfig() { return firstValueFrom(this.http.get<{allow_signup: boolean}>(this.root + '/auth/config')); }
  auth(action: 'login' | 'register', email: string, password: string) {
    return firstValueFrom(this.http.post<{email: string}>(this.root + '/auth/' + action, { email, password }));
  }
  logout() { return firstValueFrom(this.http.post(this.root + '/auth/logout', {})); }
  list() { return firstValueFrom(this.http.get<PresentationSummary[]>(this.root + '/presentations')); }
  create(request: CreatePresentation) { return firstValueFrom(this.http.post<Presentation>(this.root + '/presentations', request)); }
  deck(id: string) { return firstValueFrom(this.http.get<Presentation>(this.url(id))); }
  capabilities() { return firstValueFrom(this.http.get<GenerationCapabilities>(this.root + '/capabilities')); }
  skills() { return firstValueFrom(this.http.get<LibrarySkill[]>(this.root + '/skills')); }
  addSkill(skill: Omit<Skill, 'id'>) { return firstValueFrom(this.http.post<Skill>(this.root + '/skills', skill)); }
  removeSkill(id: string) { return firstValueFrom(this.http.delete(this.root + '/skills/' + id)); }
  skillFromAsset(id: string, branded: boolean) {
    return firstValueFrom(this.http.post<Skill>(this.root + '/skills/from-asset/' + id, {}, { params: { branded } }));
  }
  skillLink(id: string) { return this.root + '/skills/' + id + '/export'; }
  saveSkill(id: string) { return firstValueFrom(this.http.post<Skill>(this.url(id, '/skill'), {})); }
  questions(id: string) { return firstValueFrom(this.http.get<QuestionBatch>(this.url(id, '/questions'))); }
  answer(id: string, answers: Answer[]) { return firstValueFrom(this.http.post(this.url(id, '/answers'), { answers })); }
  save(id: string, revision: number, document: Deck, label: string) {
    return firstValueFrom(this.http.patch<Presentation>(this.url(id), { revision, document, label }));
  }
  editExhibit(id: string, revision: number, slide: Slide, visual: ExhibitVisual, recipe: ExhibitRecipe, illustrative: boolean) {
    return firstValueFrom(this.http.post<Slide>(this.url(id, '/slides/' + slide.id + '/exhibit'),
      { revision, slide, visual, recipe, illustrative }));
  }
  upload(file: File) {
    const data = new FormData();
    data.append('file', file);
    return firstValueFrom(this.http.post<Asset>(this.root + '/assets', data));
  }
  generate(id: string) { return firstValueFrom(this.http.post(this.url(id, '/generate'), {})); }
  cancel(id: string) { return firstValueFrom(this.http.post<Presentation>(this.url(id, '/cancel'), {})); }
  duplicate(id: string) { return firstValueFrom(this.http.post<Presentation>(this.url(id, '/duplicate'), {})); }
  remove(id: string) { return firstValueFrom(this.http.delete(this.url(id))); }
  versions(id: string) { return firstValueFrom(this.http.get<Version[]>(this.url(id, '/versions'))); }
  restore(id: string, revision: number, expected: number) {
    return firstValueFrom(this.http.post<Presentation>(this.url(id, '/versions/' + revision + '/restore?expected=' + expected), {}));
  }
  chat(id: string, instruction: string, slide_id?: string) {
    return firstValueFrom(this.http.post<EditPreview>(this.url(id, '/chat'), { instruction, slide_id }));
  }
  apply(id: string, preview: EditPreview) {
    return firstValueFrom(this.http.post<Presentation>(this.url(id, '/chat/apply'), { revision: preview.revision, proposal: preview.proposal }));
  }
  export(id: string, format: 'pdf' | 'pptx') {
    return firstValueFrom(this.http.post(this.url(id, '/export?format=' + format), {}, { responseType: 'blob', observe: 'response' }));
  }
  reviewExport(id: string, revision: number) {
    return firstValueFrom(this.http.post<ExportReview>(this.url(id, '/export/review'), {}, { params: { expected: revision } }));
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    const detail: unknown = error.error?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(item => String(item.msg ?? 'Invalid input')).join(' ');
    if (error.status === 0) return 'The studio server is unavailable. Check that it is running.';
  }
  return 'Something went wrong. Please try again.';
}
