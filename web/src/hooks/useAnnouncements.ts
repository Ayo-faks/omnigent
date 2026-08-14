import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

/** Banner styling tier for an announcement. */
export type AnnouncementLevel = "info" | "warning" | "success";

/** A server-wide announcement, as returned by ``GET /v1/announcements``. */
export interface Announcement {
  object: "announcement";
  id: string;
  message: string;
  level: AnnouncementLevel;
  active: boolean;
  dismissible: boolean;
  link_url: string | null;
  link_label: string | null;
  created_at: number;
  updated_at: number;
}

/** One row in a ``PUT /v1/announcements`` body (id omitted for new rows). */
export interface AnnouncementInput {
  id?: string;
  message: string;
  level: AnnouncementLevel;
  active: boolean;
  dismissible: boolean;
  link_url: string | null;
  link_label: string | null;
}

interface AnnouncementList {
  object: "list";
  data: Announcement[];
}

const ACTIVE_KEY = ["announcements", "active"];
const ALL_KEY = ["announcements", "all"];

async function readError(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return body?.error?.message ?? `${res.status} ${res.statusText}`;
}

async function fetchList(url: string): Promise<Announcement[]> {
  const res = await authenticatedFetch(url);
  if (!res.ok) throw new Error(await readError(res));
  const body = (await res.json()) as AnnouncementList;
  return body.data;
}

/**
 * Active announcements for the top-bar banner. Polled so a newly-published (or
 * retracted) notice appears without a reload; served to any signed-in user.
 */
export function useAnnouncements() {
  return useQuery({
    queryKey: ACTIVE_KEY,
    queryFn: () => fetchList("/v1/announcements"),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** Every announcement (active + inactive) for the admin editor. */
export function useAllAnnouncements() {
  return useQuery({
    queryKey: ALL_KEY,
    queryFn: () => fetchList("/v1/announcements/all"),
    staleTime: 5_000,
  });
}

/** PUT /v1/announcements — replace the full list (admin). */
export function useSetAnnouncements() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (announcements: AnnouncementInput[]) => {
      const res = await authenticatedFetch("/v1/announcements", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ announcements }),
      });
      if (!res.ok) throw new Error(await readError(res));
      return ((await res.json()) as AnnouncementList).data;
    },
    onSuccess: (data) => {
      // The editor query gets the authoritative saved list immediately; the
      // banner query is invalidated so it re-derives its active subset.
      queryClient.setQueryData(ALL_KEY, data);
      void queryClient.invalidateQueries({ queryKey: ACTIVE_KEY });
    },
  });
}
