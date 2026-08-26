// Table of contents for markdown files, extracted from heading structure.

import { useEffect, useMemo, useRef, useState } from "react";
import { SearchIcon, XIcon } from "lucide-react";
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
  /** Whether the TOC is open (for overlay mode). */
  open?: boolean;
  /** Callback when TOC should close. */
  onClose?: () => void;
}

export function MarkdownTableOfContents({
  content,
  containerRef,
  open = true,
  onClose,
}: MarkdownTableOfContentsProps) {
  const headings = useMemo(() => extractHeadings(content), [content]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [filterText, setFilterText] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);

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

  // Auto-focus search input when TOC opens
  useEffect(() => {
    if (open && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [open]);

  // Close on Escape key
  useEffect(() => {
    if (!open || !onClose) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [open, onClose]);

  // Filter headings based on search text
  const filteredHeadings = useMemo(() => {
    if (!filterText.trim()) return headings;
    const lower = filterText.toLowerCase();
    return headings.filter((h) => h.text.toLowerCase().includes(lower));
  }, [headings, filterText]);

  if (headings.length === 0 || !open) return null;

  const handleClick = (id: string) => {
    const container = containerRef?.current;
    if (!container) return;
    const target = container.querySelector(`#${CSS.escape(id)}`);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    onClose?.();
  };

  return (
    <>
      {/* Backdrop */}
      {onClose && (
        <div
          className="fixed inset-0 z-40 bg-black/20 backdrop-blur-sm"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      {/* TOC Panel */}
      <nav
        className={cn(
          "fixed top-0 right-0 bottom-0 z-50 w-80 bg-card border-l border-border flex flex-col",
          "shadow-xl",
        )}
        aria-label="Table of contents"
      >
        {/* Header with search */}
        <div className="shrink-0 border-b border-border p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-foreground">On this page</h2>
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                className="rounded p-1 hover:bg-muted transition-colors"
                aria-label="Close table of contents"
              >
                <XIcon className="size-4" />
              </button>
            )}
          </div>

          {/* Filter input */}
          <div className="relative">
            <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              ref={searchInputRef}
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Filter headings"
              className="w-full rounded-md border border-border bg-background pl-9 pr-3 py-1.5 text-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>

        {/* Scrollable headings list */}
        <div className="flex-1 overflow-y-auto px-4 py-2">
          {filteredHeadings.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">No matching headings</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {filteredHeadings.map((heading) => (
                <li
                  key={heading.id}
                  style={{ paddingLeft: `${(heading.level - 1) * 0.75}rem` }}
                  className="leading-snug"
                >
                  <button
                    type="button"
                    onClick={() => handleClick(heading.id)}
                    className={cn(
                      "block w-full text-left transition-colors hover:text-foreground rounded px-2 py-1",
                      activeId === heading.id
                        ? "font-medium text-foreground bg-muted"
                        : "text-muted-foreground",
                    )}
                  >
                    {heading.text}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </nav>
    </>
  );
}
