import { Component, provideZonelessChangeDetection } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { bootstrapApplication } from '@angular/platform-browser';
import type { Workspace } from './app/workspace/workspace';
import { ActivatedRouteSnapshot, BaseRouteReuseStrategy, RouteReuseStrategy, provideRouter, RouterOutlet, UrlMatcher, withRouterConfig, withInMemoryScrolling } from '@angular/router';

// Share the dashboard route instance so drafts survive navigation between its pages.
const dashboardPage: UrlMatcher = segments => {
  const [page] = segments;
  return !page || (segments.length === 1 && ['presentations', 'skills'].includes(page.path))
    ? { consumed: segments } : null;
};

class StudioRouteReuse extends BaseRouteReuseStrategy {
  override shouldReuseRoute(next: ActivatedRouteSnapshot, current: ActivatedRouteSnapshot) {
    return super.shouldReuseRoute(next, current) && next.params['id'] === current.params['id'];
  }
}

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
    { provide: RouteReuseStrategy, useClass: StudioRouteReuse },
    provideRouter([
      { matcher: dashboardPage, loadComponent: () => import('./app/dashboard/dashboard').then(m => m.Dashboard) },
      { path: 'presentation/:id', canDeactivate: [(workspace: Workspace) => workspace.canLeave()], loadComponent: () => import('./app/workspace/workspace').then(m => m.Workspace) },
      { path: '**', redirectTo: '' },
    ], withRouterConfig({canceledNavigationResolution: 'computed'}), withInMemoryScrolling({ scrollPositionRestoration: 'enabled' })),
  ],
}).catch(error => console.error(error));
