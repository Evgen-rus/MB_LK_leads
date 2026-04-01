import type { WheelEvent } from 'react';

export function preventNumberInputWheel(event: WheelEvent<HTMLInputElement>): void {
  event.currentTarget.blur();
}
