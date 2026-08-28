import type { ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ActionsProvider,
  KEYBINDING_CHORD_TIMEOUT_MS,
  getKeybindingSnapshot,
  replaceAllUserKeybindings,
} from "@/actions";
import { resetKeybindingStoreForTesting } from "@/actions/KeybindingStore";
import { KeybindingEditor } from "./KeybindingEditor";

vi.mock("@/components/ui/select", async () => {
  const { Children, isValidElement } = await import("react");
  const SelectTrigger = ({ children }: { children?: ReactNode }) => children;
  const Select = ({
    value,
    onValueChange,
    children,
  }: {
    value: string;
    onValueChange: (value: string) => void;
    children: ReactNode;
  }) => {
    const kids = Children.toArray(children);
    const trigger = kids.find((child) => isValidElement(child) && child.type === SelectTrigger);
    const testId =
      isValidElement(trigger) && typeof trigger.props === "object"
        ? (trigger.props as Record<string, unknown>)["data-testid"]
        : undefined;
    return (
      <select
        data-testid={typeof testId === "string" ? testId : undefined}
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
      >
        {kids.filter((child) => !(isValidElement(child) && child.type === SelectTrigger))}
      </select>
    );
  };
  return {
    Select,
    SelectTrigger,
    SelectValue: () => null,
    SelectContent: ({ children }: { children: ReactNode }) => children,
    SelectItem: ({ value, children }: { value: string; children: ReactNode }) => (
      <option value={value}>{children}</option>
    ),
  };
});

function renderEditor() {
  return render(
    <ActionsProvider>
      <KeybindingEditor />
    </ActionsProvider>,
  );
}

function row(id: string): HTMLElement {
  const element = document.querySelector(`[data-binding-id="${id}"]`);
  if (!(element instanceof HTMLElement)) throw new Error(`Missing keybinding row ${id}`);
  return element;
}

function record(label: string, key: string, init: KeyboardEventInit = {}) {
  fireEvent.click(screen.getByRole("button", { name: label }));
  fireEvent.keyDown(screen.getByRole("application", { name: "Keybinding recorder" }), {
    key,
    ...init,
  });
  act(() => vi.advanceTimersByTime(KEYBINDING_CHORD_TIMEOUT_MS));
}

function addBinding(actionTitle: string, mode: string, key: string) {
  fireEvent.click(screen.getByRole("button", { name: `Add binding for ${actionTitle}` }));
  fireEvent.change(screen.getByTestId("keybinding-edit-mode"), { target: { value: mode } });
  record("Record new binding", key, { ctrlKey: true });
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  resetKeybindingStoreForTesting();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  resetKeybindingStoreForTesting();
  vi.useRealTimers();
});

describe("KeybindingEditor", () => {
  it("searches action ids and formatted keys and filters by mode", () => {
    renderEditor();
    fireEvent.change(screen.getByRole("textbox", { name: "Search keyboard shortcuts" }), {
      target: { value: "session.action.new" },
    });
    expect(screen.getAllByText("New chat")).not.toHaveLength(0);
    expect(screen.queryByText("Open command palette")).toBeNull();

    fireEvent.change(screen.getByTestId("keybinding-mode-filter"), {
      target: { value: "terminal" },
    });
    expect(screen.getByText("No keyboard shortcuts found.")).toBeInTheDocument();
  });

  it("hides reserved Escape rows while allowing another binding for the action", () => {
    renderEditor();
    expect(screen.getByText("Stop response")).toBeInTheDocument();
    expect(screen.getAllByText("Escape dismissal is managed automatically.")).not.toHaveLength(0);
    expect(
      screen.getByRole("button", { name: "Add binding for Stop response" }),
    ).toBeInTheDocument();
    expect(document.querySelector('[data-action-id="composer.action.stop"]')).toBeNull();
  });

  it("adds a non-Escape binding for a reserved panel-dismiss action", () => {
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Add binding for Close files panel" }));
    record("Record new binding", ".", { ctrlKey: true });
    const added = getKeybindingSnapshot().effectiveRules.find(
      (rule) => rule.origin === "user" && rule.action === "panel.action.closeFiles",
    );
    expect(added).toMatchObject({ mode: "filesPanel", activation: "active" });
  });

  it("shows only live modes and disambiguates argument-bearing defaults", () => {
    renderEditor();
    expect(screen.queryByRole("option", { name: "Code editor" })).toBeNull();
    expect(document.querySelectorAll('[data-action-id="session.action.openPinned"]')).toHaveLength(
      10,
    );
    expect(screen.getAllByText(/Slot 3/)).not.toHaveLength(0);

    const slotThree = row("session.openPinned.browser.3");
    fireEvent.click(
      within(slotThree).getByRole("button", { name: /^Add alternate for Open pinned session/ }),
    );
    record("Record new binding", "p", { ctrlKey: true, shiftKey: true });
    const added = getKeybindingSnapshot().userRules.find((rule) => rule.id.startsWith("user."));
    expect(added?.args).toEqual({ slot: 2 });
  });

  it("rebinds, unbinds, and resets one default binding", () => {
    renderEditor();
    const newChat = row("session.new");
    fireEvent.click(within(newChat).getByRole("button", { name: /^Rebind / }));
    record("Record replacement", "n", { ctrlKey: true, shiftKey: true });
    expect(within(row("session.new")).getByText("Modified")).toBeInTheDocument();
    expect(within(row("session.new")).getByText("Shift")).toBeInTheDocument();

    fireEvent.click(within(row("session.new")).getByRole("button", { name: /^Unbind / }));
    expect(within(row("session.new")).getAllByText("Unbound")).not.toHaveLength(0);
    fireEvent.click(within(row("session.new")).getByRole("button", { name: /^Reset / }));
    expect(within(row("session.new")).getByText("Default")).toBeInTheDocument();
  });

  it("treats re-recording a visible primary shortcut as the unchanged default", () => {
    renderEditor();
    const palette = row("workbench.showCommands");
    fireEvent.click(within(palette).getByRole("button", { name: /^Rebind / }));
    record("Record replacement", "k", { ctrlKey: true });
    expect(within(row("workbench.showCommands")).getByText("Default")).toBeInTheDocument();
    expect(getKeybindingSnapshot().userRules).toEqual([]);
  });

  it("adds the same key in disjoint Composer and Terminal modes", () => {
    renderEditor();
    addBinding("New chat", "composer", "j");
    addBinding("New chat", "terminal", "j");
    expect(screen.queryByText("Conflicting keyboard shortcut")).toBeNull();
    expect(document.querySelectorAll('[data-binding-id^="user.session.action.new."]')).toHaveLength(
      2,
    );
  });

  it.each([
    ["session.new", "j", { shiftKey: true }],
    ["composer.recallPrevious", "e", {}],
  ])("warns before text-entry binding %s can intercept typing", (id, key, init) => {
    renderEditor();
    fireEvent.click(within(row(id)).getByRole("button", { name: /^Rebind / }));
    record("Record replacement", key, init);
    expect(screen.getByText("Review keyboard shortcut")).toBeInTheDocument();
    expect(screen.getByText(/text-entry shortcut without Ctrl/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(getKeybindingSnapshot().userRules).toEqual([]);
  });

  it("warns when the browser or operating system may reserve a key", () => {
    renderEditor();
    fireEvent.click(within(row("session.new")).getByRole("button", { name: /^Rebind / }));
    record("Record replacement", "F5");
    expect(screen.getByText(/browser or operating system may reserve/)).toBeInTheDocument();
  });

  it("explains chord-prefix conflicts before saving", () => {
    renderEditor();
    fireEvent.click(within(row("session.new")).getByRole("button", { name: /^Rebind / }));
    fireEvent.click(screen.getByRole("button", { name: "Record replacement" }));
    const recorder = screen.getByRole("application", { name: "Keybinding recorder" });
    fireEvent.keyDown(recorder, { key: "k", ctrlKey: true });
    fireEvent.keyDown(recorder, { key: "n", ctrlKey: true });
    expect(screen.getByText(/chord prefix Ctrl\+K takes precedence/)).toBeInTheDocument();
  });

  it.each([
    ["j", "KeyJ", {}, /text-entry shortcut without Ctrl/],
    ["t", "KeyT", { ctrlKey: true }, /browser or operating system may reserve/],
  ])("warns for unsafe physical-key recording %s", (key, code, init, warning) => {
    renderEditor();
    fireEvent.click(
      within(row("workbench.toggleConversationsSidebar")).getByRole("button", {
        name: /^Rebind /,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Record replacement" }));
    fireEvent.keyDown(screen.getByRole("application", { name: "Keybinding recorder" }), {
      key,
      code,
      ...init,
    });
    act(() => vi.advanceTimersByTime(KEYBINDING_CHORD_TIMEOUT_MS));
    expect(screen.getByText(warning)).toBeInTheDocument();
  });

  it("preserves physical matching when rebinding a code-based default", () => {
    renderEditor();
    fireEvent.click(
      within(row("workbench.toggleConversationsSidebar")).getByRole("button", {
        name: /^Rebind /,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Record replacement" }));
    const recorder = screen.getByRole("application", { name: "Keybinding recorder" });
    fireEvent.keyDown(recorder, {
      key: "]",
      code: "BracketRight",
      ctrlKey: true,
      altKey: true,
    });
    act(() => vi.advanceTimersByTime(KEYBINDING_CHORD_TIMEOUT_MS));
    fireEvent.click(screen.getByRole("button", { name: "Save anyway" }));
    expect(
      getKeybindingSnapshot().userRules.find(
        (rule) => rule.id === "workbench.toggleConversationsSidebar",
      )?.sequence,
    ).toBe("mod+alt+[BracketRight]");
  });

  it("warns before an overlapping Global save and identifies the winner", () => {
    renderEditor();
    fireEvent.click(
      within(row("workbench.showCommands")).getByRole("button", { name: /^Rebind / }),
    );
    record("Record replacement", "n", { ctrlKey: true });
    expect(screen.getByText("Conflicting keyboard shortcut")).toBeInTheDocument();
    const conflict = screen.getByText(
      (_content, element) =>
        element?.tagName === "LI" &&
        element.textContent?.includes("Conflicts with New chat") === true,
    );
    expect(conflict).toHaveTextContent("Open command palette takes precedence");
    fireEvent.click(screen.getByRole("button", { name: "Save anyway" }));
    expect(within(row("workbench.showCommands")).getByText("Modified")).toBeInTheDocument();
  });

  it("shows persistence failures inside the conflict dialog", () => {
    renderEditor();
    fireEvent.click(
      within(row("workbench.showCommands")).getByRole("button", { name: /^Rebind / }),
    );
    record("Record replacement", "n", { ctrlKey: true });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    fireEvent.click(screen.getByRole("button", { name: "Save anyway" }));
    expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent(
      "could not be saved",
    );
  });

  it("resets an action, the filtered mode, and all bindings", () => {
    renderEditor();
    addBinding("New chat", "composer", "j");
    addBinding("New chat", "terminal", "g");
    const addButton = screen.getByRole("button", { name: "Add binding for New chat" });
    const actionCard = addButton.closest(".rounded-lg");
    expect(actionCard).not.toBeNull();
    fireEvent.click(
      within(actionCard as HTMLElement).getByRole("button", { name: "Reset action" }),
    );
    expect(document.querySelectorAll('[data-binding-id^="user.session.action.new."]')).toHaveLength(
      0,
    );

    addBinding("New chat", "composer", "j");
    addBinding("Open command palette", "terminal", "g");
    fireEvent.change(screen.getByTestId("keybinding-mode-filter"), {
      target: { value: "composer" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reset current mode" }));
    expect(document.querySelectorAll('[data-binding-id^="user.session.action.new."]')).toHaveLength(
      0,
    );
    fireEvent.change(screen.getByTestId("keybinding-mode-filter"), {
      target: { value: "all" },
    });
    expect(
      document.querySelectorAll('[data-binding-id^="user.workbench.action.showCommands."]'),
    ).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Reset all" }));
    expect(screen.getByText("Reset all keyboard shortcuts?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset all bindings" }));
    expect(document.querySelectorAll('[data-binding-id^="user."]')).toHaveLength(0);
  });

  it("deduplicates imported ids and renders default-id collisions with stored data", () => {
    expect(
      replaceAllUserKeybindings([
        { id: "user.duplicate", action: "session.action.new", sequence: "mod+j", mode: "global" },
        { id: "user.duplicate", action: "session.action.new", sequence: "mod+k", mode: "global" },
        {
          id: "session.new",
          action: "workbench.action.showCommands",
          sequence: "ctrl+x",
          mode: "global",
        },
      ]),
    ).toEqual({ ok: true, changed: true });
    renderEditor();
    expect(document.querySelectorAll('[data-binding-id="user.duplicate"]')).toHaveLength(1);
    const collision = document.querySelector(
      '[data-binding-id="session.new:workbench.action.showCommands:global"]',
    ) as HTMLElement;
    expect(within(collision).getByText("Dormant")).toBeInTheDocument();
    expect(within(collision).getByText("X")).toBeInTheDocument();
  });

  it("labels and removes a structurally valid dormant binding", () => {
    expect(
      replaceAllUserKeybindings([
        {
          id: "user.bad-slot",
          action: "session.action.openPinned",
          sequence: "mod+9",
          mode: "global",
          args: { slot: 99 },
        },
      ]),
    ).toEqual({ ok: true, changed: true });
    renderEditor();
    const dormant = row("user.bad-slot");
    expect(within(dormant).getByText("Dormant")).toBeInTheDocument();
    expect(within(dormant).getByText("9")).toBeInTheDocument();
    fireEvent.click(within(dormant).getByRole("button", { name: /^Remove / }));
    expect(document.querySelector('[data-binding-id="user.bad-slot"]')).toBeNull();
  });

  it("shows a platform-inactive override as individually removable Dormant state", () => {
    expect(
      replaceAllUserKeybindings([
        {
          id: "session.openPinned.native.1",
          action: "session.action.openPinned",
          sequence: "mod+1",
          mode: "global",
          args: { slot: 0 },
        },
      ]),
    ).toEqual({ ok: true, changed: true });
    renderEditor();
    const inactive = row("session.openPinned.native.1");
    expect(within(inactive).getByText("Dormant")).toBeInTheDocument();
    fireEvent.click(within(inactive).getByRole("button", { name: /^Remove / }));
    expect(document.querySelector('[data-binding-id="session.openPinned.native.1"]')).toBeNull();
  });

  it("resets a normal action while passing through an inert imported Escape row", () => {
    localStorage.setItem(
      "omnigent:keybindings:v1",
      JSON.stringify([
        {
          id: "panel.closeFiles",
          action: "panel.action.closeFiles",
          sequence: "escape",
          mode: "filesPanel",
        },
        {
          id: "session.new",
          action: "session.action.new",
          sequence: "mod+shift+n",
          mode: "global",
        },
      ]),
    );
    resetKeybindingStoreForTesting();
    renderEditor();
    const addButton = screen.getByRole("button", { name: "Add binding for New chat" });
    const actionCard = addButton.closest(".rounded-lg") as HTMLElement;
    fireEvent.click(within(actionCard).getByRole("button", { name: "Reset action" }));
    expect(getKeybindingSnapshot().userRules.map((rule) => rule.id)).toEqual(["panel.closeFiles"]);
  });

  it("does not mode-reset a reserved imported binding hidden from the row list", () => {
    localStorage.setItem(
      "omnigent:keybindings:v1",
      JSON.stringify([
        {
          id: "panel.closeFiles",
          action: "panel.action.closeFiles",
          sequence: "escape",
          mode: "filesPanel",
        },
      ]),
    );
    resetKeybindingStoreForTesting();
    renderEditor();
    fireEvent.change(screen.getByTestId("keybinding-mode-filter"), {
      target: { value: "filesPanel" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reset current mode" }));
    expect(getKeybindingSnapshot().userRules.map((rule) => rule.id)).toEqual(["panel.closeFiles"]);
  });

  it("preserves unknown imported rows when resetting the current mode", () => {
    expect(
      replaceAllUserKeybindings([
        { id: "future", action: "future.action.run", sequence: "mod+j", mode: "global" },
      ]),
    ).toEqual({ ok: true, changed: true });
    renderEditor();
    fireEvent.change(screen.getByTestId("keybinding-mode-filter"), {
      target: { value: "global" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reset current mode" }));
    expect(getKeybindingSnapshot().userRules.map((rule) => rule.id)).toEqual(["future"]);
  });

  it("restores focus to the edited row after recording completes", async () => {
    renderEditor();
    const rebind = within(row("session.new")).getByRole("button", { name: /^Rebind / });
    fireEvent.click(rebind);
    record("Record replacement", "n", { ctrlKey: true, shiftKey: true });
    await act(async () => Promise.resolve());
    expect(rebind).toHaveFocus();
  });

  it("uses unique accessible names for row actions", () => {
    renderEditor();
    expect(
      screen.getByRole("button", { name: "Rebind New chat (session.new)" }),
    ).toBeInTheDocument();
    expect(within(row("session.new")).getByRole("img", { name: /Keybinding/ })).toBeInTheDocument();
  });

  it("changes the mode of an existing alternate while rebinding", () => {
    renderEditor();
    addBinding("New chat", "composer", "j");
    const alternate = document.querySelector(
      '[data-binding-id^="user.session.action.new."]',
    ) as HTMLElement;
    fireEvent.click(within(alternate).getByRole("button", { name: /^Rebind / }));
    fireEvent.change(screen.getByTestId("keybinding-edit-mode"), {
      target: { value: "terminal" },
    });
    record("Record replacement", "l", { ctrlKey: true, shiftKey: true });
    expect(
      getKeybindingSnapshot().userRules.find((rule) => rule.id.startsWith("user.")),
    ).toMatchObject({ mode: "terminal", sequence: "mod+shift+l" });
  });

  it("removes an alternate binding", () => {
    renderEditor();
    addBinding("New chat", "composer", "j");
    const alternate = document.querySelector(
      '[data-binding-id^="user.session.action.new."]',
    ) as HTMLElement;
    fireEvent.click(within(alternate).getByRole("button", { name: /^Remove / }));
    expect(document.querySelector('[data-binding-id^="user.session.action.new."]')).toBeNull();
  });
});
