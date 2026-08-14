// Tests for the admin AnnouncementsPage. Browser e2e is impractical (admin-
// gated), so the surface is pinned here by mocking the identity probe and the
// react-query announcement hooks — no QueryClient or network needed.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnnouncementsPage } from "./AnnouncementsPage";
import type { Announcement } from "@/hooks/useAnnouncements";
import * as identity from "@/lib/identity";
import * as hook from "@/hooks/useAnnouncements";

const serverInfoMocks = vi.hoisted(() => ({ singleUser: false }));

vi.mock("@/lib/CapabilitiesContext", () => ({
  useServerInfo: () => ({ single_user: serverInfoMocks.singleUser, login_url: "/login" }),
}));
vi.mock("@/lib/identity", () => ({
  resolveIdentity: vi.fn(),
  getCurrentIsAdmin: vi.fn(),
}));
vi.mock("@/hooks/useAnnouncements", () => ({
  useAllAnnouncements: vi.fn(),
  useSetAnnouncements: vi.fn(),
}));

const saveMutate = vi.fn();

function setAll(data: Announcement[] | undefined, isLoading = false) {
  vi.mocked(hook.useAllAnnouncements).mockReturnValue({
    data,
    isLoading,
  } as unknown as ReturnType<typeof hook.useAllAnnouncements>);
}

beforeEach(() => {
  vi.mocked(identity.resolveIdentity).mockResolvedValue("admin@example.com");
  vi.mocked(identity.getCurrentIsAdmin).mockReturnValue(true);
  saveMutate.mockReset();
  vi.mocked(hook.useSetAnnouncements).mockReturnValue({
    mutate: saveMutate,
    isPending: false,
  } as unknown as ReturnType<typeof hook.useSetAnnouncements>);
  serverInfoMocks.singleUser = false;
});

afterEach(cleanup);

describe("AnnouncementsPage", () => {
  it("shows a no-permission message to a non-admin", async () => {
    vi.mocked(identity.getCurrentIsAdmin).mockReturnValue(false);
    setAll([]);
    render(<AnnouncementsPage />);
    await waitFor(() =>
      expect(
        screen.getByText("You don't have permission to manage announcements."),
      ).toBeInTheDocument(),
    );
  });

  it("shows the empty state and lets an admin add a row", async () => {
    setAll([]);
    render(<AnnouncementsPage />);

    await waitFor(() => expect(screen.getByTestId("announcement-add")).toBeInTheDocument());
    expect(screen.getByText(/No announcements yet/i)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("announcement-add"));
    expect(screen.getByTestId("announcement-editor-row")).toBeInTheDocument();
  });

  it("blocks saving when a row has an empty message", async () => {
    setAll([]);
    render(<AnnouncementsPage />);
    await waitFor(() => expect(screen.getByTestId("announcement-add")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("announcement-add")); // empty message row
    fireEvent.click(screen.getByTestId("announcement-save"));

    expect(screen.getByText("Every announcement needs a message.")).toBeInTheDocument();
    expect(saveMutate).not.toHaveBeenCalled();
  });

  it("saves the drafted announcements via the mutation", async () => {
    setAll([]);
    render(<AnnouncementsPage />);
    await waitFor(() => expect(screen.getByTestId("announcement-add")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("announcement-add"));
    fireEvent.change(screen.getByTestId("announcement-message"), {
      target: { value: "Welcome aboard" },
    });
    fireEvent.click(screen.getByTestId("announcement-save"));

    expect(saveMutate).toHaveBeenCalledTimes(1);
    const [payload] = saveMutate.mock.calls[0];
    expect(payload).toEqual([
      expect.objectContaining({
        message: "Welcome aboard",
        level: "info",
        active: true,
        dismissible: true,
        link_url: null,
        link_label: null,
      }),
    ]);
  });

  it("prefills existing announcements from the server", async () => {
    setAll([
      {
        object: "announcement",
        id: "anc_1",
        message: "Maintenance tonight",
        level: "warning",
        active: true,
        dismissible: true,
        link_url: null,
        link_label: null,
        created_at: 1,
        updated_at: 1,
      },
    ]);
    render(<AnnouncementsPage />);

    await waitFor(() =>
      expect((screen.getByTestId("announcement-message") as HTMLTextAreaElement).value).toBe(
        "Maintenance tonight",
      ),
    );
  });
});
