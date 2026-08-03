// Convert a user's typed answer to a native TUI stuck-prompt into the ordered
// tmux key names the stuck-prompt supervisor types into the pane
// (`content.keys` — see `omnigent/antigravity_native_stuck.py`).
//
// tmux `send-keys` accepts both literal characters ("1", "y", "a") and named
// keys ("Enter", "Escape", "Up"). Most first-run prompts are answered by typing
// a short token (a menu digit like "1", a "y"/"n", or a word) and pressing
// Enter, so a typed answer maps to: one key per character, then "Enter". Prompts
// that are arrow-driven (some theme pickers) are answered with the quick-key
// buttons instead, which submit a single named key without a trailing Enter.

/** A single named key the quick-key buttons can send (no trailing Enter). */
export type NamedKey = "Up" | "Down" | "Left" | "Right" | "Enter" | "Escape" | "Space";

/**
 * Convert a typed answer string into tmux key names, one per character, with a
 * trailing "Enter" to submit. Whitespace-only input yields an empty list (the
 * caller then submits nothing rather than a bare Enter).
 *
 * @param answer The user's typed answer, e.g. "1" or "y" or "dark".
 * @returns Ordered tmux key names, e.g. ["1", "Enter"] or [] when blank.
 */
export function answerToTmuxKeys(answer: string): string[] {
  if (!answer.trim()) return [];
  // Split into individual characters — tmux send-keys types each literal char.
  // A space becomes the named "Space" key so it is not swallowed as an arg
  // separator by the send-keys invocation on the Python side.
  const keys = Array.from(answer).map((ch) => (ch === " " ? "Space" : ch));
  keys.push("Enter");
  return keys;
}
