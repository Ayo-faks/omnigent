import { afterEach, describe, expect, it } from "vitest";
import {
  applyChatWidth,
  CHAT_WIDTH_DEFAULT,
  normalizeChatWidth,
  readChatWidth,
  writeChatWidth,
} from "./chatWidthPreferences";

const STORAGE_KEY = "omnigent:chat-width";

afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-chat-width");
});

describe("chatWidthPreferences — read/write", () => {
  it("returns standard when nothing is stored", () => {
    expect(readChatWidth()).toBe(CHAT_WIDTH_DEFAULT);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("stores wide and clears the key for standard", () => {
    writeChatWidth("wide");
    expect(readChatWidth()).toBe("wide");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("wide");

    writeChatWidth("standard");
    expect(readChatWidth()).toBe("standard");
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});

describe("normalizeChatWidth", () => {
  it("passes through valid values", () => {
    expect(normalizeChatWidth("standard")).toBe("standard");
    expect(normalizeChatWidth("wide")).toBe("wide");
  });

  it("maps unknown, null, and garbage to standard", () => {
    expect(normalizeChatWidth("full")).toBe("standard");
    expect(normalizeChatWidth("bogus")).toBe("standard");
    expect(normalizeChatWidth(null)).toBe("standard");
    expect(normalizeChatWidth(undefined)).toBe("standard");
  });
});

describe("applyChatWidth", () => {
  it("sets data-chat-width for wide and removes it for standard", () => {
    applyChatWidth("wide");
    expect(document.documentElement.getAttribute("data-chat-width")).toBe("wide");

    applyChatWidth("standard");
    expect(document.documentElement.hasAttribute("data-chat-width")).toBe(false);
  });
});
