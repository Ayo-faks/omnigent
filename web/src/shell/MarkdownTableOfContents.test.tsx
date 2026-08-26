import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarkdownTableOfContents } from "./MarkdownTableOfContents";
import { useRef } from "react";

describe("MarkdownTableOfContents", () => {
  it("should extract headings from markdown content", () => {
    const content = `# Main Title
Some content here.

## Section 1
More content.

### Subsection 1.1
Details.

## Section 2
Final section.`;

    function Wrapper() {
      const ref = useRef<HTMLDivElement>(null);
      return (
        <div>
          <div ref={ref} />
          <MarkdownTableOfContents content={content} containerRef={ref} />
        </div>
      );
    }

    render(<Wrapper />);

    expect(screen.getByText("Main Title")).toBeInTheDocument();
    expect(screen.getByText("Section 1")).toBeInTheDocument();
    expect(screen.getByText("Subsection 1.1")).toBeInTheDocument();
    expect(screen.getByText("Section 2")).toBeInTheDocument();
  });

  it("should not render when there are no headings", () => {
    const content = "Just some plain text without any headings.";

    function Wrapper() {
      const ref = useRef<HTMLDivElement>(null);
      return (
        <div>
          <div ref={ref} />
          <MarkdownTableOfContents content={content} containerRef={ref} />
        </div>
      );
    }

    const { container } = render(<Wrapper />);
    expect(container.querySelector("nav")).not.toBeInTheDocument();
  });

  it("should generate unique slugs for duplicate headings", () => {
    const content = `# Introduction
## Details
## Details
## Details`;

    function Wrapper() {
      const ref = useRef<HTMLDivElement>(null);
      return (
        <div>
          <div ref={ref} />
          <MarkdownTableOfContents content={content} containerRef={ref} />
        </div>
      );
    }

    const { container } = render(<Wrapper />);
    const buttons = container.querySelectorAll("button");
    // Should have 4 headings total
    expect(buttons.length).toBe(4);
  });
});
