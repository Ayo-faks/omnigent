import { describe, expect, it } from "vitest";
import { answerToTmuxKeys } from "./terminalKeys";

describe("answerToTmuxKeys", () => {
  it("maps a menu digit to the digit plus Enter", () => {
    expect(answerToTmuxKeys("1")).toEqual(["1", "Enter"]);
  });

  it("maps a short word to one key per char plus Enter", () => {
    expect(answerToTmuxKeys("yes")).toEqual(["y", "e", "s", "Enter"]);
  });

  it("maps an interior space to the named Space key", () => {
    expect(answerToTmuxKeys("a b")).toEqual(["a", "Space", "b", "Enter"]);
  });

  it("returns no keys for blank input (so nothing is typed)", () => {
    expect(answerToTmuxKeys("")).toEqual([]);
    expect(answerToTmuxKeys("   ")).toEqual([]);
  });
});
