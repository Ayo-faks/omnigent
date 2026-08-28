import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ActionsProvider } from "@/actions";
import { resetKeybindingStoreForTesting } from "@/actions/KeybindingStore";
import { KeybindingEditor } from "./KeybindingEditor";

beforeEach(() => {
  localStorage.clear();
  resetKeybindingStoreForTesting();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  resetKeybindingStoreForTesting();
});

describe("KeybindingEditor Radix controls", () => {
  it("opens the real mode select from the keyboard", async () => {
    render(
      <ActionsProvider>
        <KeybindingEditor />
      </ActionsProvider>,
    );
    const trigger = screen.getByTestId("keybinding-mode-filter");
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(await screen.findByRole("option", { name: "Composer" })).toBeInTheDocument();
  });
});
