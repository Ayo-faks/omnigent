import {
  formatKeybindingForAria,
  isMacKeyboardPlatform,
  keybindingParts,
  serializeKeybinding,
  type KeybindingMode,
  type KeybindingRule,
} from "@/actions";

export const KEYBINDING_MODE_LABELS: Readonly<Record<KeybindingMode, string>> = {
  global: "Global",
  composer: "Composer",
  terminal: "Terminal",
  codeEditor: "Code editor",
  markdownEditor: "Markdown editor",
  fileViewer: "File viewer",
  commandPalette: "Command palette",
  dialog: "Dialog",
  filesPanel: "Files panel",
  terminalsPanel: "Terminals panel",
  executionLogs: "Execution logs",
  markdownToc: "Markdown table of contents",
};

export function KeybindingSequence({
  sequence,
  emptyLabel = "Unbound",
}: {
  sequence: KeybindingRule["sequence"] | null;
  emptyLabel?: string;
}) {
  if (!sequence) return <span className="text-sm text-muted-foreground">{emptyLabel}</span>;
  const strokes = keybindingParts(sequence, { isMac: isMacKeyboardPlatform() }).map(
    (parts, position) => ({
      parts,
      id: `${serializeKeybinding([sequence[position]!])}-${position === 0 ? "first" : "second"}`,
    }),
  );
  return (
    <span
      role="img"
      className="inline-flex flex-wrap items-center justify-end gap-1"
      aria-label={`Keybinding ${formatKeybindingForAria(sequence, { isMac: isMacKeyboardPlatform() })}`}
    >
      {strokes.map(({ id, parts }) => (
        <span key={id} className="inline-flex items-center gap-1">
          {id.endsWith("-second") && <span className="px-0.5 text-muted-foreground">then</span>}
          {parts.map((part) => (
            <kbd
              key={part}
              className="inline-flex h-6 min-w-6 items-center justify-center rounded-md border border-border bg-muted px-1.5 font-sans text-xs font-medium text-muted-foreground"
            >
              {part}
            </kbd>
          ))}
        </span>
      ))}
    </span>
  );
}
