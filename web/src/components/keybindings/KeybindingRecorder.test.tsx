import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ActionsProvider,
  HANDLED,
  KEYBINDING_CHORD_TIMEOUT_MS,
  KeybindingDispatcher,
  useRegisterAction,
} from "@/actions";
import { KeybindingRecorder } from "./KeybindingRecorder";

function PaletteHandler({ run }: { run: () => typeof HANDLED }) {
  useRegisterAction("workbench.action.showCommands", { acceptsKeybindings: true, run });
  return null;
}

function renderRecorder({
  onComplete = vi.fn(),
  onCancel = vi.fn(),
  run = vi.fn(() => HANDLED),
  preferPhysical = false,
} = {}) {
  render(
    <ActionsProvider>
      <KeybindingDispatcher />
      <PaletteHandler run={run} />
      <KeybindingRecorder
        onComplete={onComplete}
        onCancel={onCancel}
        preferPhysical={preferPhysical}
      />
    </ActionsProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Record binding" }));
  return {
    recorder: screen.getByRole("application", { name: "Keybinding recorder" }),
    onComplete,
    onCancel,
    run,
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("KeybindingRecorder", () => {
  it("suspends normal dispatch and records a single stroke after the chord timeout", () => {
    vi.useFakeTimers();
    const { recorder, onComplete, run } = renderRecorder();
    expect(fireEvent.keyDown(recorder, { key: "k", ctrlKey: true })).toBe(false);
    expect(run).not.toHaveBeenCalled();
    expect(screen.getByText("Waiting for second key…")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(KEYBINDING_CHORD_TIMEOUT_MS));
    expect(onComplete).toHaveBeenCalledWith("mod+k");
    expect(screen.queryByTestId("keybinding-recorder")).toBeNull();
  });

  it("records a two-stroke chord immediately", () => {
    vi.useFakeTimers();
    const { recorder, onComplete } = renderRecorder();
    fireEvent.keyDown(recorder, { key: "k", ctrlKey: true });
    fireEvent.keyDown(recorder, { key: "s", ctrlKey: true });
    expect(onComplete).toHaveBeenCalledWith("mod+k mod+s");
    act(() => vi.advanceTimersByTime(KEYBINDING_CHORD_TIMEOUT_MS));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("cancels on Escape without invoking the existing Escape actions", () => {
    const { recorder, onComplete, onCancel } = renderRecorder();
    expect(fireEvent.keyDown(recorder, { key: "Escape" })).toBe(false);
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onComplete).not.toHaveBeenCalled();
  });

  it.each(["Backspace", "Delete"])("clears the pending candidate with %s", (key) => {
    vi.useFakeTimers();
    const { recorder, onComplete } = renderRecorder();
    fireEvent.keyDown(recorder, { key: "k", ctrlKey: true });
    expect(fireEvent.keyDown(recorder, { key })).toBe(false);
    expect(screen.getByText("Press keys…")).toBeInTheDocument();
    expect(screen.getByTestId("keybinding-recorder")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(KEYBINDING_CHORD_TIMEOUT_MS));
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("allows modified Backspace to be recorded", () => {
    vi.useFakeTimers();
    const { recorder, onComplete } = renderRecorder();
    fireEvent.keyDown(recorder, { key: "Backspace", ctrlKey: true });
    act(() => vi.advanceTimersByTime(KEYBINDING_CHORD_TIMEOUT_MS));
    expect(onComplete).toHaveBeenCalledWith("mod+Backspace");
  });

  it("ignores composition, AltGraph, modifier-only, and repeat events", () => {
    const { recorder, onComplete } = renderRecorder();
    const composing = new KeyboardEvent("keydown", {
      key: "Process",
      bubbles: true,
      cancelable: true,
      isComposing: true,
    });
    recorder.dispatchEvent(composing);
    const altGraph = new KeyboardEvent("keydown", {
      key: "@",
      ctrlKey: true,
      altKey: true,
      bubbles: true,
      cancelable: true,
    });
    altGraph.getModifierState = (key) => key === "AltGraph";
    recorder.dispatchEvent(altGraph);
    expect(fireEvent.keyDown(recorder, { key: "Control", ctrlKey: true })).toBe(true);
    expect(fireEvent.keyDown(recorder, { key: "k", ctrlKey: true, repeat: true })).toBe(true);
    expect(onComplete).not.toHaveBeenCalled();
    expect(composing.defaultPrevented).toBe(false);
    expect(altGraph.defaultPrevented).toBe(false);
  });

  it("records bracket and Alt-modified keys by physical code", () => {
    vi.useFakeTimers();
    const bracket = renderRecorder({ preferPhysical: true });
    fireEvent.keyDown(bracket.recorder, {
      key: "[",
      code: "BracketLeft",
      ctrlKey: true,
      altKey: true,
    });
    act(() => vi.advanceTimersByTime(KEYBINDING_CHORD_TIMEOUT_MS));
    expect(bracket.onComplete).toHaveBeenCalledWith("mod+alt+[BracketLeft]");

    cleanup();
    const altKey = renderRecorder();
    fireEvent.keyDown(altKey.recorder, { key: "v", code: "KeyV", altKey: true });
    act(() => vi.advanceTimersByTime(KEYBINDING_CHORD_TIMEOUT_MS));
    expect(altKey.onComplete).toHaveBeenCalledWith("alt+[KeyV]");
  });

  it.each([
    ["CapsLock", ""],
    ["[", ""],
  ])("keeps recording and explains unsupported key %s", (key, code) => {
    const { recorder, onComplete } = renderRecorder();
    expect(fireEvent.keyDown(recorder, { key, code, ctrlKey: true })).toBe(true);
    expect(screen.getByTestId("keybinding-recorder")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("cannot be recorded");
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("cancels when focus leaves with no related target", () => {
    const { recorder, onCancel } = renderRecorder();
    fireEvent.blur(recorder, { relatedTarget: null });
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("exposes screen-reader recording instructions", () => {
    renderRecorder();
    expect(screen.getByText(/Press a key combination/)).toHaveClass("sr-only");
  });
});
