import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MarkdownTableOfContents } from "./MarkdownTableOfContents";
import { useRef, useEffect } from "react";

describe("MarkdownTableOfContents", () => {
  it("should extract headings from rendered DOM", async () => {
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

      // Simulate rendered markdown by creating DOM structure
      useEffect(() => {
        if (ref.current) {
          ref.current.innerHTML = `
            <h1 id="main-title">Main Title</h1>
            <p>Some content here.</p>
            <h2 id="section-1">Section 1</h2>
            <p>More content.</p>
            <h3 id="subsection-11">Subsection 1.1</h3>
            <p>Details.</p>
            <h2 id="section-2">Section 2</h2>
            <p>Final section.</p>
          `;
        }
      }, []);

      return (
        <div>
          <div ref={ref} />
          <MarkdownTableOfContents content={content} containerRef={ref} />
        </div>
      );
    }

    const { container } = render(<Wrapper />);

    // Wait for the component to extract headings from the DOM
    await waitFor(() => {
      const nav = container.querySelector("nav");
      expect(nav).toBeInTheDocument();

      // Check that all headings are in the TOC (as buttons)
      const buttons = nav?.querySelectorAll("button");
      expect(buttons?.length).toBe(4);
    });

    // Verify the TOC contains the expected headings
    const nav = container.querySelector("nav");
    expect(nav?.textContent).toContain("Main Title");
    expect(nav?.textContent).toContain("Section 1");
    expect(nav?.textContent).toContain("Subsection 1.1");
    expect(nav?.textContent).toContain("Section 2");
  });

  it("should not render when there are no headings", () => {
    const content = "Just some plain text without any headings.";

    function Wrapper() {
      const ref = useRef<HTMLDivElement>(null);

      // Simulate rendered markdown with no headings
      useEffect(() => {
        if (ref.current) {
          ref.current.innerHTML = "<p>Just some plain text without any headings.</p>";
        }
      }, []);

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

  it("should handle duplicate heading IDs from rehype-slug", async () => {
    const content = `# Introduction
## Details
## Details
## Details`;

    function Wrapper() {
      const ref = useRef<HTMLDivElement>(null);

      // Simulate rehype-slug's duplicate handling: details, details-1, details-2
      useEffect(() => {
        if (ref.current) {
          ref.current.innerHTML = `
            <h1 id="introduction">Introduction</h1>
            <h2 id="details">Details</h2>
            <h2 id="details-1">Details</h2>
            <h2 id="details-2">Details</h2>
          `;
        }
      }, []);

      return (
        <div>
          <div ref={ref} />
          <MarkdownTableOfContents content={content} containerRef={ref} />
        </div>
      );
    }

    const { container } = render(<Wrapper />);

    // Wait for headings to be extracted
    await waitFor(() => {
      const buttons = container.querySelectorAll("button");
      expect(buttons.length).toBe(4);
    });
  });
});
