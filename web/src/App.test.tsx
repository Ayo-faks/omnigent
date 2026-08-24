import { render, screen } from "@testing-library/react";
import { Outlet, MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { FALLBACK_SERVER_INFO } from "@/lib/capabilities";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";

vi.mock("@/lib/analytics", () => ({ useOmnigentPageView: vi.fn() }));
vi.mock("@/shell/AppShell", () => ({
  AppShell: () => (
    <div>
      <span>app shell</span>
      <Outlet />
    </div>
  ),
}));
vi.mock("@/pages/ChatPage", () => ({ ChatPage: () => <div>chat page</div> }));
vi.mock("@/pages/NotFoundPage", () => ({ NotFoundPage: () => <div>not found</div> }));
vi.mock("@/pages/UsagePage", () => ({ UsagePage: () => <div>usage page</div> }));
vi.mock("@/pages/ApprovePage", () => ({ ApprovePage: () => <div>approve page</div> }));
vi.mock("@/pages/InboxPage", () => ({ InboxPage: () => <div>inbox page</div> }));
vi.mock("@/pages/TasksPage", () => ({ TasksPage: () => <div>tasks page</div> }));
vi.mock("@/pages/SettingsPage", () => ({ SettingsPage: () => <div>settings page</div> }));
vi.mock("@/pages/dpia/DpiaPortfolioPage", () => ({
  DpiaPortfolioPage: () => <div>dpia portfolio page</div>,
}));
vi.mock("@/pages/dpia/DpiaNewAssessmentPage", () => ({
  DpiaNewAssessmentPage: () => <div>dpia new page</div>,
}));
vi.mock("@/pages/dpia/DpiaCasePage", () => ({
  DpiaCasePage: () => <div>dpia case page</div>,
}));
vi.mock("@/pages/dpia/DpiaRequestPage", () => ({
  DpiaRequestPage: () => <div>dpia request page</div>,
}));
vi.mock("@/pages/dpia/DpiaRequestReviewPage", () => ({
  DpiaRequestReviewPage: () => <div>dpia request review page</div>,
}));
vi.mock("@/pages/dpia/DpiaRespondPage", () => ({
  DpiaRespondPage: () => <div>dpia respond page</div>,
}));

import App from "./App";

function renderUsageRoute(enabled: boolean) {
  const info: typeof FALLBACK_SERVER_INFO = {
    ...FALLBACK_SERVER_INFO,
    features: enabled ? { usage_page: true } : {},
  };
  return render(
    <CapabilitiesProvider info={info}>
      <MemoryRouter initialEntries={["/usage"]}>
        <App />
      </MemoryRouter>
    </CapabilitiesProvider>,
  );
}

describe("Usage release feature route", () => {
  it("does not register /usage while the feature is off", async () => {
    renderUsageRoute(false);
    expect(await screen.findByText("not found")).toBeInTheDocument();
    expect(screen.queryByText("usage page")).toBeNull();
  });

  it("registers /usage while the feature is on", async () => {
    renderUsageRoute(true);
    expect(await screen.findByText("usage page")).toBeInTheDocument();
    expect(screen.queryByText("not found")).toBeNull();
  });
});

describe("application route table regression", () => {
  it.each([
    ["/", "chat page"],
    ["/c/conv_original", "chat page"],
    ["/inbox", "inbox page"],
    ["/tasks", "tasks page"],
    ["/usage", "usage page"],
    ["/settings", "settings page"],
    ["/settings/appearance", "settings page"],
    ["/approve/conv_original/elicit_original", "approve page"],
    ["/dpia", "dpia portfolio page"],
    ["/dpia/new", "dpia new page"],
    ["/dpia/request", "dpia request page"],
    ["/dpia/requests/req-vendor-abc", "dpia request review page"],
    ["/dpia/respond/session-1", "dpia respond page"],
    ["/dpia/cases/student-success-alert", "dpia case page"],
  ])("keeps %s registered", async (path, expectedPage) => {
    render(
      <CapabilitiesProvider info={{ ...FALLBACK_SERVER_INFO, features: { usage_page: true } }}>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </CapabilitiesProvider>,
    );

    expect(await screen.findByText(expectedPage)).toBeInTheDocument();
    expect(screen.queryByText("not found")).toBeNull();
  });
});
