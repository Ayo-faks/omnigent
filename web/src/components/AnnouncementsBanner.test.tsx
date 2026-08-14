// Tests for the top-bar AnnouncementsBanner. The react-query hook is mocked so
// no QueryClient or network is needed; localStorage (jsdom) backs dismissals.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnnouncementsBanner } from "./AnnouncementsBanner";
import type { Announcement } from "@/hooks/useAnnouncements";
import * as hook from "@/hooks/useAnnouncements";

vi.mock("@/hooks/useAnnouncements", () => ({ useAnnouncements: vi.fn() }));

function announcement(overrides: Partial<Announcement> = {}): Announcement {
  return {
    object: "announcement",
    id: "anc_1",
    message: "Heads up",
    level: "info",
    active: true,
    dismissible: true,
    link_url: null,
    link_label: null,
    created_at: 100,
    updated_at: 100,
    ...overrides,
  };
}

function setData(data: Announcement[] | undefined) {
  vi.mocked(hook.useAnnouncements).mockReturnValue({
    data,
  } as unknown as ReturnType<typeof hook.useAnnouncements>);
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(cleanup);

describe("AnnouncementsBanner", () => {
  it("renders nothing when there are no announcements", () => {
    setData([]);
    const { container } = render(<AnnouncementsBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing while the query is loading (undefined data)", () => {
    setData(undefined);
    const { container } = render(<AnnouncementsBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders each active announcement with its message", () => {
    setData([
      announcement({ id: "anc_1", message: "First" }),
      announcement({ id: "anc_2", message: "Second", level: "warning" }),
    ]);
    render(<AnnouncementsBanner />);
    expect(screen.getByText("First")).toBeInTheDocument();
    expect(screen.getByText("Second")).toBeInTheDocument();
    expect(screen.getAllByTestId("announcement")).toHaveLength(2);
  });

  it("renders a link with its label", () => {
    setData([announcement({ link_url: "https://x.example", link_label: "Details" })]);
    render(<AnnouncementsBanner />);
    const link = screen.getByRole("link", { name: "Details" });
    expect(link).toHaveAttribute("href", "https://x.example");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("dismissing hides the row and persists to localStorage", () => {
    setData([announcement({ id: "anc_1", updated_at: 100 })]);
    render(<AnnouncementsBanner />);

    fireEvent.click(screen.getByTestId("announcement-dismiss"));

    expect(screen.queryByTestId("announcement")).not.toBeInTheDocument();
    const stored = JSON.parse(window.localStorage.getItem("omnigent:dismissed-announcements")!);
    expect(stored).toContain("anc_1:100");
  });

  it("stays hidden on next mount when previously dismissed", () => {
    window.localStorage.setItem("omnigent:dismissed-announcements", JSON.stringify(["anc_1:100"]));
    setData([announcement({ id: "anc_1", updated_at: 100 })]);
    render(<AnnouncementsBanner />);
    expect(screen.queryByTestId("announcement")).not.toBeInTheDocument();
  });

  it("re-surfaces a dismissed announcement after it is edited (updated_at changes)", () => {
    window.localStorage.setItem("omnigent:dismissed-announcements", JSON.stringify(["anc_1:100"]));
    // Same id, newer updated_at → the stored token no longer matches.
    setData([announcement({ id: "anc_1", updated_at: 200, message: "Edited" })]);
    render(<AnnouncementsBanner />);
    expect(screen.getByText("Edited")).toBeInTheDocument();
  });

  it("shows no dismiss control for a non-dismissible announcement", () => {
    setData([announcement({ dismissible: false })]);
    render(<AnnouncementsBanner />);
    expect(screen.getByTestId("announcement")).toBeInTheDocument();
    expect(screen.queryByTestId("announcement-dismiss")).not.toBeInTheDocument();
  });
});
