/* Calculator service — exercises class extends + methods in TypeScript. */

class BaseCalculator {
  reset(): void {
    return;
  }
}

export class Calculator extends BaseCalculator {
  add(a: number, b: number): number {
    return a + b;
  }
}
