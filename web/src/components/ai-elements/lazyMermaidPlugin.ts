import type { DiagramPlugin, MermaidConfig, MermaidInstance } from "@streamdown/mermaid";

// Streamdown's `mermaid` plugin (@streamdown/mermaid) statically imports
// mermaid, which drags cytoscape and the hand-drawn renderer into the eager
// entry graph even when no message ever contains a diagram.
//
// This wrapper defers the @streamdown/mermaid import until a diagram actually
// renders, mirroring `lazyCodePlugin`, so mermaid splits into its own on-demand
// chunk. Streamdown only ever reaches the instance as
// `getMermaid(config).render(...)` from an async path, so a promise-returning
// `render` satisfies the contract; `language` is the one field it needs
// synchronously, to match the ```mermaid fence.

let realMermaid: DiagramPlugin | null = null;
let mermaidPromise: Promise<DiagramPlugin> | null = null;

const loadMermaid = (): Promise<DiagramPlugin> => {
  // oxlint-disable-next-line eslint-plugin-promise(prefer-await-to-then)
  mermaidPromise ??= import("@streamdown/mermaid").then(({ mermaid }) => {
    realMermaid = mermaid;
    return mermaid;
  });
  return mermaidPromise;
};

export const lazyMermaidPlugin: DiagramPlugin = {
  name: "mermaid",
  type: "diagram",
  language: "mermaid",
  getMermaid: (config?: MermaidConfig): MermaidInstance => ({
    // Streamdown never calls initialize (it passes config to getMermaid and
    // renders), but the contract allows it, so forward it once loaded.
    initialize: (nextConfig: MermaidConfig) => {
      // oxlint-disable-next-line eslint-plugin-promise(prefer-await-to-then)
      void loadMermaid().then((plugin) => plugin.getMermaid(config).initialize(nextConfig));
    },
    render: async (id: string, source: string) => {
      const plugin = realMermaid ?? (await loadMermaid());
      return plugin.getMermaid(config).render(id, source);
    },
  }),
};
