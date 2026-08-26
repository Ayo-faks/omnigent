/**
 * Test-only overrides for long UI timing constants.
 *
 * A handful of timers (the SSE stall guard, the presence-idle debounce, the
 * idle-notification settle window) are tens of seconds long by design, so the
 * e2e tests that assert their behaviour otherwise sit idle waiting them out. A
 * Playwright test can shorten a timer by setting `window.__omniTestTimers`
 * (via `add_init_script`, which runs before the bundle evaluates) — the value
 * is read once at module init and each constant falls back to its production
 * default when the global is absent.
 *
 * This is inert in production: nothing sets the global, so every timer resolves
 * to its default. It is deliberately NOT wired to server config or `/v1/info`
 * — it exists only to let tests trade a real wall-clock wait for a short one
 * without weakening the assertion (a 3 s stall guard proves the same
 * self-heal a 45 s one does).
 */

/** Timer keys a test may override. Values are milliseconds. */
export interface TestTimerOverrides {
  sseStallMs?: number;
  presenceIdleMs?: number;
  idleNotificationSettleMs?: number;
}

declare global {
  interface Window {
    __omniTestTimers?: TestTimerOverrides;
  }
}

/**
 * Resolve a timing constant, honouring a test override when present.
 *
 * @param key The override key a test would set on `window.__omniTestTimers`.
 * @param defaultMs The production value used whenever no override is set.
 * @returns The override (a finite, positive number) or `defaultMs`.
 */
export function resolveTestTimer(key: keyof TestTimerOverrides, defaultMs: number): number {
  if (typeof window === "undefined") return defaultMs;
  const override = window.__omniTestTimers?.[key];
  return typeof override === "number" && Number.isFinite(override) && override > 0
    ? override
    : defaultMs;
}
