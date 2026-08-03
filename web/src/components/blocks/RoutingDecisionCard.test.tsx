import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RoutingDecisionCard } from "./RoutingDecisionCard";
import type { RoutingDecisionItem } from "@/lib/conversationItems";

describe("RoutingDecisionCard", () => {
  it("renders the model shorthand", () => {
    const item: RoutingDecisionItem = {
      id: "item_123",
      type: "routing_decision",
      responseId: "resp_123",
      model: "databricks-claude-opus-4-8",
      applied: true,
      rationale: "Prompt indicates complex reasoning",
    };
    render(<RoutingDecisionCard item={item} />);
    expect(screen.getByText(/opus/)).toBeInTheDocument();
  });

  it("displays the rationale", () => {
    const item: RoutingDecisionItem = {
      id: "item_123",
      type: "routing_decision",
      responseId: "resp_123",
      model: "databricks-claude-sonnet-5",
      applied: true,
      rationale: "Rule-based fallback",
    };
    render(<RoutingDecisionCard item={item} />);
    expect(screen.getByText(/Rule-based fallback/)).toBeInTheDocument();
  });

  it("truncates long rationales", () => {
    const longRationale = "A".repeat(60);
    const item: RoutingDecisionItem = {
      id: "item_123",
      type: "routing_decision",
      responseId: "resp_123",
      model: "databricks-gpt-5-6-sol",
      applied: false,
      rationale: longRationale,
    };
    const { getByText } = render(<RoutingDecisionCard item={item} />);
    expect(getByText(/A{40}…/)).toBeInTheDocument();
  });

  it("includes agent name when present", () => {
    const item: RoutingDecisionItem = {
      id: "item_123",
      type: "routing_decision",
      responseId: "resp_123",
      model: "databricks-claude-sonnet-5",
      applied: true,
      rationale: "Test",
      agent: "Explore",
    };
    render(<RoutingDecisionCard item={item} />);
    const card = screen.getByTestId("routing-decision-card");
    expect(card).toHaveAttribute("aria-label", expect.stringContaining("Explore"));
  });
});
