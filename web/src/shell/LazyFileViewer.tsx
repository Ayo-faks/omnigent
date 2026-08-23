// Lazy boundary in front of `FileViewer`.
//
// The viewer owns the app's only reachable path to the TipTap / ProseMirror
// rich-text stack (via `MarkdownRichTextViewer`'s editable markdown mode) —
// ~940 KB of source, plus linkify and the rest of `CodeViewer`. Importing it
// statically from AppShell put all of that in the eagerly-loaded entry chunk,
// so every page load paid for an editor that only appears once someone opens a
// file.
//
// Both mount sites already gate on a selected file, so nothing fetches this
// until the viewer is genuinely on screen. `CodeViewer` keeps its own inner lazy
// boundaries for Monaco / PDF / 3D on top of this one.

import { lazy, Suspense, type ComponentProps } from "react";
import type { FileViewer as FileViewerImpl } from "./FileViewer";

const FileViewerChunk = lazy(() => import("./FileViewer").then((m) => ({ default: m.FileViewer })));

export type FileViewerProps = ComponentProps<typeof FileViewerImpl>;

/**
 * `FileViewer`, loaded on demand. The fallback matches the "Loading…" panel
 * `CodeViewer` already shows behind its own lazy boundaries.
 */
export function LazyFileViewer(props: FileViewerProps) {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center p-8 text-muted-foreground text-ui">
          Loading…
        </div>
      }
    >
      <FileViewerChunk {...props} />
    </Suspense>
  );
}
