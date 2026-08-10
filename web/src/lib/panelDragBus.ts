/**
 * Broadcasts "a panel resize drag is in progress" so the embedded browser can
 * step out of the way. The desktop browser page is a native WebContentsView
 * painted over the DOM: once the cursor enters its rect, every mousemove and
 * mouseup goes to the embedded page instead of the app window, so the drag
 * never ends, the transparent drag overlay is never removed, and the whole UI
 * becomes unclickable. A DOM overlay can't cover a view that isn't in the DOM,
 * so the view detaches for the duration of the drag instead.
 *
 * Ref-counted: concurrent drags (or a StrictMode double-invoke) can't leave the
 * view detached.
 */

type Listener = (dragging: boolean) => void;

const listeners = new Set<Listener>();
let depth = 0;

function emit(): void {
  const dragging = depth > 0;
  for (const listener of listeners) {
    try {
      listener(dragging);
    } catch (err) {
      console.warn("[panel-drag] listener threw:", err);
    }
  }
}

/** Mark a resize drag as started. Pair with `endPanelDrag`. */
export function beginPanelDrag(): void {
  depth += 1;
  if (depth === 1) emit();
}

/** Mark a resize drag as finished. Safe to call when no drag is active. */
export function endPanelDrag(): void {
  if (depth === 0) return;
  depth -= 1;
  if (depth === 0) emit();
}

/** Subscribe to drag start/stop; returns an unsubscribe. */
export function onPanelDragChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Whether any resize drag is currently live. */
export function isPanelDragging(): boolean {
  return depth > 0;
}

/** Reset the ref count. Only for use in tests. */
export function resetPanelDragForTesting(): void {
  depth = 0;
  listeners.clear();
}
