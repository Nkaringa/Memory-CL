/* Math helpers — exercises import + function + call extraction. */

import { logCall } from "./logger.js";

export function multiply(a, b) {
  logCall("multiply");
  return a * b;
}
