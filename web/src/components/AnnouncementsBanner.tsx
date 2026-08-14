/**
 * Top-bar announcements banner.
 *
 * Renders the server-wide notices an admin publishes (Settings → Announcements)
 * as full-width strips pinned above the app chrome. Each active, non-dismissed
 * announcement is one strip, styled by its level (info / warning / success).
 *
 * Dismissals are per-device (localStorage), keyed by ``id:updated_at`` so an
 * admin editing a dismissed notice re-surfaces it, while an untouched one stays
 * hidden. A non-dismissible notice shows no close button and can't be hidden.
 */

import { useCallback, useState } from "react";
import { CheckCircle2Icon, InfoIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { type Announcement, useAnnouncements } from "@/hooks/useAnnouncements";
import { cn } from "@/lib/utils";

const DISMISSED_KEY = "omnigent:dismissed-announcements";

/** Dismissal token for an announcement — re-surfaces when the row is edited. */
function dismissalToken(a: Announcement): string {
  return `${a.id}:${a.updated_at}`;
}

function readDismissed(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(DISMISSED_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed)
      ? new Set(parsed.filter((x): x is string => typeof x === "string"))
      : new Set();
  } catch {
    return new Set();
  }
}

function writeDismissed(tokens: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(DISMISSED_KEY, JSON.stringify([...tokens]));
  } catch {
    // localStorage failures are non-fatal — the banner just won't persist.
  }
}

const LEVEL_STYLES: Record<
  Announcement["level"],
  { bar: string; icon: typeof InfoIcon; iconClass: string }
> = {
  info: { bar: "bg-info/10 border-info/30", icon: InfoIcon, iconClass: "text-info" },
  warning: {
    bar: "bg-warning/10 border-warning/40",
    icon: TriangleAlertIcon,
    iconClass: "text-warning",
  },
  success: {
    bar: "bg-success/10 border-success/30",
    icon: CheckCircle2Icon,
    iconClass: "text-success",
  },
};

/** A single announcement strip. */
function AnnouncementRow({
  announcement,
  onDismiss,
}: {
  announcement: Announcement;
  onDismiss: (a: Announcement) => void;
}) {
  const style = LEVEL_STYLES[announcement.level] ?? LEVEL_STYLES.info;
  const Icon = style.icon;
  // Server-validated to http(s) or a site-relative path. External links open in
  // a new tab; relative ones navigate in place.
  const isExternal = /^https?:/i.test(announcement.link_url ?? "");
  return (
    <div
      role="status"
      data-testid="announcement"
      className={cn(
        "flex w-full items-center gap-2.5 border-b px-4 py-2 text-sm text-foreground",
        style.bar,
      )}
    >
      <Icon className={cn("size-4 shrink-0", style.iconClass)} aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="[overflow-wrap:anywhere]">{announcement.message}</span>
        {announcement.link_url && (
          <a
            href={announcement.link_url}
            target={isExternal ? "_blank" : undefined}
            rel={isExternal ? "noopener noreferrer" : undefined}
            className="ml-2 font-medium underline underline-offset-2 hover:no-underline"
          >
            {announcement.link_label ?? announcement.link_url}
          </a>
        )}
      </span>
      {announcement.dismissible && (
        <button
          type="button"
          aria-label="Dismiss announcement"
          data-testid="announcement-dismiss"
          onClick={() => onDismiss(announcement)}
          className="shrink-0 rounded-sm p-0.5 text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground"
        >
          <XIcon className="size-4" />
        </button>
      )}
    </div>
  );
}

/**
 * The banner stack. Renders nothing (no reserved space) when there are no
 * active, non-dismissed announcements, so the layout is unchanged in the common
 * case. Mounted at the top of the app shell.
 */
export function AnnouncementsBanner() {
  const { data } = useAnnouncements();
  const [dismissed, setDismissed] = useState<Set<string>>(readDismissed);

  const dismiss = useCallback((a: Announcement) => {
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(dismissalToken(a));
      writeDismissed(next);
      return next;
    });
  }, []);

  const visible = (data ?? []).filter((a) => !dismissed.has(dismissalToken(a)));
  if (visible.length === 0) return null;

  return (
    <div className="shrink-0" data-testid="announcements-banner">
      {visible.map((a) => (
        <AnnouncementRow key={a.id} announcement={a} onDismiss={dismiss} />
      ))}
    </div>
  );
}
