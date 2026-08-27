import { useMemo } from "react";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { useLocation, useParams } from "@/lib/routing";
import { resolveExtensionPage } from "./catalog";
import { useExtensions } from "./ExtensionProvider";
import { ExtensionPageHost } from "./ExtensionPageHost";

export function ExtensionPageRoute() {
  const params = useParams<{ extensionId: string; "*": string }>();
  const location = useLocation();
  const route = params["*"]?.split("/")[0];
  const resolved = resolveExtensionPage(useExtensions(), params.extensionId, route);

  const invocationContext = useMemo<Readonly<Record<string, string>>>(() => {
    const context: Record<string, string> = {};
    const conversationId = new URLSearchParams(location.search).get("conversationId");
    if (conversationId) context.conversationId = conversationId;
    return context;
  }, [location.search]);

  if (!resolved) return <NotFoundPage />;

  return <ExtensionPageHost resolved={resolved} invocationContext={invocationContext} />;
}
