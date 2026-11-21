import { Component, computed, input, output, signal } from '@angular/core';
import { Answer, Choice, Question } from '../models';
import { icons, StudioIcon } from '../icons';

@Component({
  selector: 'studio-questions',
  imports: [StudioIcon],
  templateUrl: './questions.html',
})
export class Questions {
  questions = input.required<Question[]>();
  busy = input(false);
  submitted = output<Answer[]>();
  readonly i = icons;
  readonly index = signal(0);
  readonly answers = signal<Record<string, Answer>>({});
  readonly current = computed(() => this.questions()[Math.min(this.index(), this.questions().length - 1)]);
  readonly answer = computed(() => this.current() ? this.forQuestion(this.current().id) : null);

  forQuestion(id: string): Answer {
    return this.answers()[id] ?? { question_id: id, option_ids: [], text: '', use_default: false };
  }

  options(question: Question): question is Choice { return 'options' in question; }

  option(question: Choice, id: string) {
    const answer = this.forQuestion(question.id);
    const values = question.type === 'multi_select'
      ? answer.option_ids.includes(id) ? answer.option_ids.filter(value => value !== id) : [...answer.option_ids, id]
      : [id];
    this.set({ ...answer, option_ids: values, use_default: false });
  }

  text(id: string, value: string) { this.set({ ...this.forQuestion(id), text: value, use_default: false }); }

  valid(question: Question) {
    const answer = this.forQuestion(question.id);
    if (question.type === 'slide_count' && answer.text.trim()) {
      const count = Number(answer.text);
      return Number.isInteger(count) && count >= 1 && count <= 30;
    }
    return !question.required || answer.use_default || answer.option_ids.length > 0 || answer.text.trim().length > 0;
  }

  next() {
    if (this.index() + 1 < this.questions().length) this.index.update(value => value + 1);
    else if (this.questions().every(item => this.valid(item))) this.submitted.emit(this.questions().map(item => this.forQuestion(item.id)));
  }

  skip(question: Question) {
    this.set({ question_id: question.id, option_ids: [], text: '', use_default: false });
    this.next();
  }

  defaults() {
    this.submitted.emit(this.questions().map(question => {
      const answer = this.forQuestion(question.id);
      return answer.option_ids.length || answer.text.trim() ? answer : {...answer, use_default: true};
    }));
  }

  private set(answer: Answer) { this.answers.update(items => ({...items, [answer.question_id]: answer})); }
}
