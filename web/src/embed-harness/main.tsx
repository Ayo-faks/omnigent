// Test-only host shell for the embed island.
//
// Mounts `OmnigentApp` — the real embed entry (src/embed.tsx), the same
// component the Databricks monolith renders — inside a minimal host page so
// e2e tests can drive embed-only behavior (the document favicon swap, the
// host navigating away and unmounting the island) without the monolith.
// Built by vite.embed-harness.config.ts and served statically by
// tests/e2e_ui/embed/. Unlike the intermediate embed build
// (vite.embed.config.ts) this is an app build: React + react-router are
// bundled, since there is no host rspack to supply them.

import { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";

import { OmnigentApp } from "../embed";

/**
 * Minimal stand-in for the host workspace shell: a chrome bar the island does
 * not own, plus a toggle that unmounts/remounts the island the way host-side
 * navigation does.
 */
function HostShell() {
  const [embedMounted, setEmbedMounted] = useState(true);
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "8px 16px",
          borderBottom: "1px solid #ccc",
          fontFamily: "sans-serif",
        }}
      >
        <strong data-testid="host-chrome">Host workspace shell</strong>
        <button
          type="button"
          data-testid="host-nav-toggle"
          onClick={() => setEmbedMounted((v) => !v)}
        >
          {embedMounted ? "Navigate away" : "Navigate back"}
        </button>
      </header>
      <div style={{ flex: 1, minHeight: 0 }}>
        {embedMounted ? (
          <OmnigentApp />
        ) : (
          <p data-testid="host-other-page" style={{ fontFamily: "sans-serif" }}>
            Host page without the embed
          </p>
        )}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <MemoryRouter>
    <HostShell />
  </MemoryRouter>,
);
