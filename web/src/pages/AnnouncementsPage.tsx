/**
 * Admin announcements editor (``/settings/announcements``). Rendered as a
 * Settings sub-category alongside Members, Policies, and Sharing.
 *
 * Lets an admin manage the server-wide notices shown in every user's top bar
 * (see {@link AnnouncementsBanner}). Gated on the client by an admin check
 * (non-admins see a "no permission" message) AND on the server by the route
 * handler — client-side gating is just UX.
 *
 * The editor holds the full list as a local draft; "Save" replaces the
 * server-side list in one atomic ``PUT /v1/announcements`` (mirroring the
 * sharing editor's single-shot save).
 */

import { useEffect, useMemo, useState } from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  type AnnouncementInput,
  type AnnouncementLevel,
  useAllAnnouncements,
  useSetAnnouncements,
} from "@/hooks/useAnnouncements";
import { isSingleUserMode } from "@/lib/capabilities";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { getCurrentIsAdmin, resolveIdentity } from "@/lib/identity";

const LEVELS: { value: AnnouncementLevel; label: string }[] = [
  { value: "info", label: "Info" },
  { value: "warning", label: "Warning" },
  { value: "success", label: "Success" },
];

/** A form row — an {@link AnnouncementInput} plus a stable React key. */
interface Draft {
  key: string;
  id?: string;
  message: string;
  level: AnnouncementLevel;
  active: boolean;
  dismissible: boolean;
  link_url: string;
  link_label: string;
}

let draftSeq = 0;
function newDraft(): Draft {
  draftSeq += 1;
  return {
    key: `draft-${draftSeq}`,
    message: "",
    level: "info",
    active: true,
    dismissible: true,
    link_url: "",
    link_label: "",
  };
}

function toDraft(a: {
  id: string;
  message: string;
  level: AnnouncementLevel;
  active: boolean;
  dismissible: boolean;
  link_url: string | null;
  link_label: string | null;
}): Draft {
  draftSeq += 1;
  return {
    key: `existing-${a.id}-${draftSeq}`,
    id: a.id,
    message: a.message,
    level: a.level,
    active: a.active,
    dismissible: a.dismissible,
    link_url: a.link_url ?? "",
    link_label: a.link_label ?? "",
  };
}

function toInput(d: Draft): AnnouncementInput {
  const url = d.link_url.trim();
  const label = d.link_label.trim();
  return {
    id: d.id,
    message: d.message.trim(),
    level: d.level,
    active: d.active,
    dismissible: d.dismissible,
    link_url: url || null,
    link_label: url && label ? label : null,
  };
}

export function AnnouncementsPage() {
  const info = useServerInfo();
  const isSingleUser = isSingleUserMode(info);
  const [meIsAdmin, setMeIsAdmin] = useState<boolean | null>(null);

  const { data, isLoading } = useAllAnnouncements();
  const save = useSetAnnouncements();
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Admin probe via the mode-agnostic `/v1/me` identity (works under OIDC too).
  // Skipped in single-user mode where no auth endpoints exist.
  useEffect(() => {
    if (isSingleUser) return;
    void (async () => {
      const userId = await resolveIdentity();
      if (userId === null) return;
      setMeIsAdmin(getCurrentIsAdmin());
    })();
  }, [isSingleUser]);

  // Seed the draft list from the server once it arrives. Only while untouched
  // (drafts === null) so a background refetch never clobbers an in-progress
  // edit; "Reset" re-seeds explicitly.
  useEffect(() => {
    if (data && drafts === null) setDrafts(data.map(toDraft));
  }, [data, drafts]);

  const patch = (key: string, next: Partial<Draft>) => {
    setError(null);
    setDrafts((prev) => (prev ?? []).map((d) => (d.key === key ? { ...d, ...next } : d)));
  };
  const remove = (key: string) => {
    setError(null);
    setDrafts((prev) => (prev ?? []).filter((d) => d.key !== key));
  };
  const add = () => {
    setError(null);
    setDrafts((prev) => [newDraft(), ...(prev ?? [])]);
  };

  const dirty = useMemo(() => {
    if (drafts === null || data === undefined) return false;
    return JSON.stringify(data.map(toDraft).map(toInput)) !== JSON.stringify(drafts.map(toInput));
  }, [drafts, data]);

  function onSave() {
    if (drafts === null) return;
    if (drafts.some((d) => d.message.trim() === "")) {
      setError("Every announcement needs a message.");
      return;
    }
    setError(null);
    save.mutate(drafts.map(toInput), {
      onSuccess: (saved) => setDrafts(saved.map(toDraft)),
      onError: (err) => setError(err.message),
    });
  }

  if (!isSingleUser && meIsAdmin === null) {
    return (
      <div className="flex min-h-full items-center justify-center text-sm text-muted-foreground">
        Loading...
      </div>
    );
  }

  if (!isSingleUser && meIsAdmin === false) {
    return (
      <PageScroll contentClassName="px-8" extraBottom="2.5rem">
        <h1 className="mb-2 text-2xl font-semibold">Announcements</h1>
        <p className="text-sm text-muted-foreground">
          You don't have permission to manage announcements.
        </p>
      </PageScroll>
    );
  }

  return (
    <PageScroll contentClassName="px-8" extraBottom="2.5rem">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Announcements</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Post notices shown in the top bar for every user on this server — maintenance windows,
            new features, incidents. Active announcements appear immediately; inactive ones are kept
            so you can toggle them back on without retyping.
          </p>
        </div>
        <Button type="button" onClick={add} data-testid="announcement-add" className="gap-1.5">
          <PlusIcon className="size-4" />
          New announcement
        </Button>
      </div>

      {isLoading || drafts === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : drafts.length === 0 ? (
        <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
          No announcements yet. Add one to show a notice in the top bar.
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          {drafts.map((d) => (
            <div
              key={d.key}
              data-testid="announcement-editor-row"
              className="flex flex-col gap-3 rounded-xl border bg-card/40 p-4"
            >
              <div className="flex items-start gap-3">
                <Textarea
                  aria-label="Announcement message"
                  data-testid="announcement-message"
                  placeholder="What do you want everyone to see?"
                  value={d.message}
                  rows={2}
                  onChange={(e) => patch(d.key, { message: e.target.value })}
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Remove announcement"
                  data-testid="announcement-remove"
                  onClick={() => remove(d.key)}
                  className="shrink-0 text-muted-foreground hover:text-destructive"
                >
                  <Trash2Icon className="size-4" />
                </Button>
              </div>

              <div className="flex flex-wrap items-end gap-4">
                <label className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-muted-foreground">Level</span>
                  <Select
                    value={d.level}
                    onValueChange={(value) => patch(d.key, { level: value as AnnouncementLevel })}
                  >
                    <SelectTrigger className="w-40" data-testid="announcement-level">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {LEVELS.map((lvl) => (
                        <SelectItem key={lvl.value} value={lvl.value}>
                          {lvl.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>

                <label className="flex items-center gap-2">
                  <Switch
                    checked={d.active}
                    onCheckedChange={(active) => patch(d.key, { active })}
                    aria-label="Active"
                    data-testid="announcement-active"
                  />
                  <span className="text-sm">Active</span>
                </label>

                <label className="flex items-center gap-2">
                  <Switch
                    checked={d.dismissible}
                    onCheckedChange={(dismissible) => patch(d.key, { dismissible })}
                    aria-label="Dismissible"
                    data-testid="announcement-dismissible"
                  />
                  <span className="text-sm">Dismissible</span>
                </label>
              </div>

              <div className="flex flex-wrap gap-4">
                <label className="flex min-w-0 flex-1 flex-col gap-1">
                  <span className="text-xs font-medium text-muted-foreground">
                    Link URL (optional)
                  </span>
                  <Input
                    type="text"
                    inputMode="url"
                    placeholder="https://… or /path"
                    spellCheck={false}
                    autoCapitalize="off"
                    autoCorrect="off"
                    value={d.link_url}
                    data-testid="announcement-link-url"
                    onChange={(e) => patch(d.key, { link_url: e.target.value })}
                  />
                </label>
                <label className="flex min-w-0 flex-1 flex-col gap-1">
                  <span className="text-xs font-medium text-muted-foreground">
                    Link label (optional)
                  </span>
                  <Input
                    type="text"
                    placeholder="Learn more"
                    value={d.link_label}
                    data-testid="announcement-link-label"
                    onChange={(e) => patch(d.key, { link_label: e.target.value })}
                  />
                </label>
              </div>
            </div>
          ))}
        </div>
      )}

      {error && <p className="mt-4 text-sm text-destructive">{error}</p>}

      {drafts !== null && (
        <div className="mt-6 flex items-center gap-3">
          <Button
            type="button"
            onClick={onSave}
            loading={save.isPending}
            disabled={!dirty || save.isPending}
            data-testid="announcement-save"
          >
            Save changes
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={!dirty || save.isPending}
            onClick={() => {
              setError(null);
              setDrafts(data ? data.map(toDraft) : []);
            }}
          >
            Discard
          </Button>
          {dirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
        </div>
      )}
    </PageScroll>
  );
}
