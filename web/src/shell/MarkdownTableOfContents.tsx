// Table of contents for markdown files, extracted from heading structure.

import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/utils";

interface TocItem {
  id: string;
  text: string;
  level: number;
}

/**
 * Extract headings from markdown content and generate anchor IDs matching
 * GitHub's slug generation (used by react-markdown with remark-gfm).
 */
function extractHeadings(markdown: string): TocItem[] {
  const headings: TocItem[] = [];
  const lines = markdown.split("\n");
  const slugCounts = new Map<string, number>();

  for (const line of lines) {
    const match = line.match(/^(#{1,6})\s+(.+)$/);
    if (!match) continue;

    const level = match[1].length;
    const text = match[2].trim();

    // Generate GitHub-compatible slug: lowercase, replace spaces with hyphens,
    // remove non-alphanumeric (except hyphens), deduplicate consecutive hyphens.
    let slug = text
      .toLowerCase()
      .replace(/\s+/g, "-")
      .replace(/[^\w-]/g, "")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "");

    // Handle duplicates by appending -1, -2, etc. (GitHub's behavior).
    const count = slugCounts.get(slug) ?? 0;
    slugCounts.set(slug, count + 1);
    if (count > 0) slug = `${slug}-${count}`;

    headings.push({ id: slug, text, level });
  }

  return headings;
}

interface MarkdownTableOfContentsProps {
  content: string;
  /** Ref to the scrollable container so TOC clicks can scroll to the target. */
  containerRef?: React.RefObject<HTMLElement | null>;
}

export function MarkdownTableOfContents({ content, containerRef }: MarkdownTableOfContentsProps) {
  const headings = useMemo(() => extractHeadings(content), [content]);
  const [activeId, setActiveId] = useState<string | null>(null);

  // Track which heading is currently visible at the top of the viewport.
  useEffect(() => {
    const container = containerRef?.current;
    if (!container || headings.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveId(entry.target.id);
          }
        }
      },
      { root: container, rootMargin: "-20% 0px -80% 0px" },
    );

    const elements = headings
      .map((h) => container.querySelector(`#${CSS.escape(h.id)}`))
      .filter((el): el is Element => el !== null);

    for (const el of elements) observer.observe(el);
    return () => observer.disconnect();
  }, [headings, containerRef]);

  if (headings.length === 0) return null;

  const handleClick = (id: string) => {
    const container = containerRef?.current;
    if (!container) return;
    const target = container.querySelector(`#${CSS.escape(id)}`);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <nav
      className="sticky top-0 max-h-screen overflow-y-auto border-l border-border bg-card/95 backdrop-blur-sm px-4 py-4"
      aria-label="Table of contents"
    >
      <h2 className="mb-3 text-sm font-semibold text-foreground">On this page</h2>
      <ul className="space-y-1.5 text-sm">
        {headings.map((heading) => (
          <li
            key={heading.id}
            style={{ paddingLeft: `${(heading.level - 1) * 0.75}rem` }}
            className="leading-snug"
          >
            <button
              type="button"
              onClick={() => handleClick(heading.id)}
              className={cn(
                "block w-full text-left transition-colors hover:text-foreground",
                activeId === heading.id ? "font-medium text-foreground" : "text-muted-foreground",
              )}
            >
              {heading.text}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
