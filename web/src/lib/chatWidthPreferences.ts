// Persisted, app-global preference for how wide the chat column renders.
//
// "standard" keeps the readable centered column (the responsive 3xl/4xl/5xl
// caps live in index.css); "wide" lets the message list and composer fill the
// available width, with message bubbles held to a share of it so prose stays
// legible. Applied as `data-chat-width` on <html>, so the CSS vars in index.css
// swap without any React plumbing. Set from Appearance settings.

const STORAGE_KEY = "omnigent:chat-width";

export const chatWidths = ["standard", "wide"] as const;
export type ChatWidth = (typeof chatWidths)[number];

/** Product default: the centered, readable column. */
export const CHAT_WIDTH_DEFAULT: ChatWidth = "standard";

/** Return whether a string is one of the selectable chat widths. */
export function isChatWidth(value: string | null | undefined): value is ChatWidth {
  return value === "standard" || value === "wide";
}

/**
 * Normalize a stored chat width to the product default. Unknown values can only
 * come from localStorage drift or manual edits.
 */
export function normalizeChatWidth(value: string | null | undefined): ChatWidth {
  return isChatWidth(value) ? value : CHAT_WIDTH_DEFAULT;
}

/**
 * Read the persisted chat width. Returns "standard" when nothing is stored, on
 * a server render (no `window`), or when the stored value is missing/unknown —
 * never throws, so a corrupt entry can't break app boot.
 */
export function readChatWidth(): ChatWidth {
  if (typeof window === "undefined") return CHAT_WIDTH_DEFAULT;
  try {
    return normalizeChatWidth(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return CHAT_WIDTH_DEFAULT;
  }
}

/**
 * Persist the chat width. "standard" clears the key (the product default).
 * Swallows quota/access errors so a failed write can't break settings.
 */
export function writeChatWidth(value: ChatWidth): void {
  if (typeof window === "undefined") return;
  try {
    const normalized = normalizeChatWidth(value);
    if (normalized === CHAT_WIDTH_DEFAULT) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, normalized);
    }
  } catch {
    // localStorage quota or access errors shouldn't break settings.
  }
}

/**
 * Apply the chat width to the DOM by setting `data-chat-width` on the document
 * root. The `[data-chat-width="wide"]` block in index.css re-points the column
 * width vars; the default "standard" removes the attribute so the base `:root`
 * vars (and their responsive caps) take over. Single source of the DOM
 * side-effect, mirroring {@link applyThemePalette}.
 */
export function applyChatWidth(value: ChatWidth): void {
  if (typeof document === "undefined") return;
  const next = normalizeChatWidth(value);
  if (next === CHAT_WIDTH_DEFAULT) {
    document.documentElement.removeAttribute("data-chat-width");
    return;
  }
  document.documentElement.setAttribute("data-chat-width", next);
}
