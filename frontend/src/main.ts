import { Component, provideZonelessChangeDetection } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter, RouterOutlet } from '@angular/router';

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
      { path: '', loadComponent: () => import('./app/dashboard/dashboard').then(m => m.Dashboard) },
      { path: 'presentation/:id', loadComponent: () => import('./app/workspace/workspace').then(m => m.Workspace) },
      { path: '**', redirectTo: '' },
    ]),
  ],
}).catch(error => console.error(error));
