import type { ReactNode } from "react";
import type { KeybindingMode, KeybindingRule } from "@/actions";
import { Button } from "@/components/ui/button";
import { KEYBINDING_MODE_LABELS, KeybindingSequence } from "./KeybindingSequence";

export type KeybindingState = "Default" | "Modified" | "Unbound" | "Alternate" | "Dormant";

export function KeybindingRow({
  title,
  actionId,
  ruleId,
  domId,
  mode,
  state,
  sequence,
  editor,
  onEdit,
  onAddAlternate,
  onUnbind,
  onReset,
  onRemove,
}: {
  title: string;
  actionId: string;
  ruleId: string;
  domId: string;
  mode: KeybindingMode;
  state: KeybindingState;
  sequence: KeybindingRule["sequence"] | null;
  editor?: ReactNode;
  onEdit?: () => void;
  onAddAlternate?: () => void;
  onUnbind?: () => void;
  onReset?: () => void;
  onRemove?: () => void;
}) {
  return (
    <li
      className="border-b border-border/60 py-3 last:border-b-0"
      data-action-id={actionId}
      data-binding-id={domId}
    >
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 basis-52 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-ui text-foreground">{title}</span>
            <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              {state}
            </span>
          </div>
          <div className="mt-0.5 flex flex-wrap gap-2 text-xs text-muted-foreground">
            <code>{actionId}</code>
            <span>·</span>
            <span>{KEYBINDING_MODE_LABELS[mode]}</span>
          </div>
        </div>
        <div className="ml-auto min-w-28 text-right">
          <KeybindingSequence sequence={sequence} />
        </div>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-1">
          {onEdit && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Rebind ${title} (${ruleId})`}
              onClick={onEdit}
            >
              Rebind
            </Button>
          )}
          {onAddAlternate && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Add alternate for ${title} (${ruleId})`}
              onClick={onAddAlternate}
            >
              Add alternate
            </Button>
          )}
          {onUnbind && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Unbind ${title} (${ruleId})`}
              onClick={onUnbind}
            >
              Unbind
            </Button>
          )}
          {onReset && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Reset ${title} (${ruleId})`}
              onClick={onReset}
            >
              Reset
            </Button>
          )}
          {onRemove && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Remove ${title} (${ruleId})`}
              onClick={onRemove}
            >
              Remove
            </Button>
          )}
        </div>
      </div>
      {editor && <div className="mt-2 flex justify-end">{editor}</div>}
    </li>
  );
}
