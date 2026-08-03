// Session-level routing decision card: renders a `routing_decision` item as a
// structured card displaying the model choice, applied status, rationale, and
// scope (session-level vs. per-turn vs. sub-agent).

import { BrainIcon } from "lucide-react";
import { shortModelName } from "@/components/CostRoutingControl";
import type { RoutingDecisionItem } from "@/lib/conversationItems";
import { cn } from "@/lib/utils";

interface RoutingDecisionCardProps {
  item: RoutingDecisionItem;
  /** CSS class for the card container. */
  className?: string;
}

/**
 * Render a routing decision as a muted chip or card.
 *
 * Session-level decisions appear once under the session's first message;
 * per-turn and sub-agent decisions appear inline under their triggering message.
 * The card always shows the model choice, applied status, and rationale.
 */
export function RoutingDecisionCard({ item, className }: RoutingDecisionCardProps) {
  const scopeLabel = item.agent ? `sub-agent (${item.agent})` : "session";
  const statusLabel = item.applied ? "applied" : "would pick";

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-full border border-border bg-muted/40 px-2.5 py-1 text-xs",
        className,
      )}
      data-testid="routing-decision-card"
      role="status"
      aria-label={`Routing decision: ${statusLabel} ${shortModelName(item.model)} (${scopeLabel})`}
    >
      <BrainIcon className="size-3 shrink-0 text-muted-foreground" />
      <span className="font-medium text-foreground">{shortModelName(item.model)}</span>
      {item.rationale && (
        <span className="text-muted-foreground" title={item.rationale}>
          · {item.rationale.slice(0, 40)}
          {item.rationale.length > 40 ? "…" : ""}
        </span>
      )}
    </div>
  );
}
