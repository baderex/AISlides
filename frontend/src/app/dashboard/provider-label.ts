import { Pipe, PipeTransform } from '@angular/core';
import { Provider } from '../models';

@Pipe({name: 'providerLabel'})
export class ProviderLabel implements PipeTransform {
  transform(providers: Provider[] | undefined, id: string | undefined): string {
    return providers?.find(provider => provider.id === id)?.label ?? '';
  }
}
