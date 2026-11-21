import { Component, provideZonelessChangeDetection } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter, RouterOutlet, UrlMatcher, withInMemoryScrolling } from '@angular/router';

// Share the dashboard route instance so drafts survive navigation between its pages.
const dashboardPage: UrlMatcher = segments => {
  const [page] = segments;
  return !page || (segments.length === 1 && ['presentations', 'skills'].includes(page.path))
    ? { consumed: segments } : null;
};

@Component({
  selector: 'studio-root',
  imports: [RouterOutlet],
  template: '<router-outlet />',
})
class App {}

bootstrapApplication(App, {
  providers: [
    provideZonelessChangeDetection(),
    provideHttpClient(),
    provideRouter([
      { matcher: dashboardPage, loadComponent: () => import('./app/dashboard/dashboard').then(m => m.Dashboard) },
      { path: 'presentation/:id', loadComponent: () => import('./app/workspace/workspace').then(m => m.Workspace) },
      { path: '**', redirectTo: '' },
    ], withInMemoryScrolling({ scrollPositionRestoration: 'enabled' })),
  ],
}).catch(error => console.error(error));
