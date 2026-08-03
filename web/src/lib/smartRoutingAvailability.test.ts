import { describe, it, expect } from "vitest";
import {
  hostBacksHarnessWithGateway,
  smartRoutingUnavailableReason,
  SMART_ROUTING_ARMS,
} from "./smartRoutingAvailability";

describe("hostBacksHarnessWithGateway", () => {
  it("returns true when gateway_inference is missing", () => {
    expect(hostBacksHarnessWithGateway({}, "claude-native")).toBe(true);
    expect(hostBacksHarnessWithGateway(null, "claude-native")).toBe(true);
    expect(hostBacksHarnessWithGateway(undefined, "claude-native")).toBe(true);
  });

  it("returns true when gateway_inference is null", () => {
    expect(hostBacksHarnessWithGateway({ gateway_inference: null }, "claude-native")).toBe(true);
  });

  it("returns true when the harness is not in gateway_inference", () => {
    expect(
      hostBacksHarnessWithGateway({ gateway_inference: { "codex-native": true } }, "claude-native"),
    ).toBe(true);
  });

  it("returns false when gateway_inference explicitly sets harness to false", () => {
    expect(
      hostBacksHarnessWithGateway({ gateway_inference: { "claude-native": false } }, "claude-native"),
    ).toBe(false);
  });

  it("returns true when gateway_inference sets harness to true", () => {
    expect(
      hostBacksHarnessWithGateway({ gateway_inference: { "claude-native": true } }, "claude-native"),
    ).toBe(true);
  });

  it("handles independent family gating", () => {
    const host = {
      gateway_inference: { "claude-native": true, "codex-native": false },
    };
    expect(hostBacksHarnessWithGateway(host, "claude-native")).toBe(true);
    expect(hostBacksHarnessWithGateway(host, "codex-native")).toBe(false);
  });
});

describe("smartRoutingUnavailableReason", () => {
  it("returns routing-disabled when routing is off", () => {
    const result = smartRoutingUnavailableReason({
      routingEnabled: false,
      wrappersRegistered: true,
      unreadyHarnesses: [],
    });
    expect(result).toEqual({ kind: "routing-disabled" });
  });

  it("returns wrappers-missing when wrappers aren't registered", () => {
    const result = smartRoutingUnavailableReason({
      routingEnabled: true,
      wrappersRegistered: false,
      unreadyHarnesses: [],
    });
    expect(result).toEqual({ kind: "wrappers-missing" });
  });

  it("returns harnesses-unready when some harnesses are unready", () => {
    const result = smartRoutingUnavailableReason({
      routingEnabled: true,
      wrappersRegistered: true,
      unreadyHarnesses: ["claude-native"],
    });
    expect(result).toEqual({
      kind: "harnesses-unready",
      harnesses: ["claude-native"],
    });
  });

  it("returns not-gateway-backed when harnesses aren't gateway-backed", () => {
    const result = smartRoutingUnavailableReason({
      routingEnabled: true,
      wrappersRegistered: true,
      unreadyHarnesses: [],
      notGatewayBackedHarnesses: ["codex-native"],
    });
    expect(result).toEqual({
      kind: "not-gateway-backed",
      harnesses: ["codex-native"],
    });
  });

  it("returns null when Smart Routing is available", () => {
    const result = smartRoutingUnavailableReason({
      routingEnabled: true,
      wrappersRegistered: true,
      unreadyHarnesses: [],
      notGatewayBackedHarnesses: [],
    });
    expect(result).toBeNull();
  });

  it("returns null when notGatewayBackedHarnesses is omitted", () => {
    const result = smartRoutingUnavailableReason({
      routingEnabled: true,
      wrappersRegistered: true,
      unreadyHarnesses: [],
    });
    expect(result).toBeNull();
  });
});
