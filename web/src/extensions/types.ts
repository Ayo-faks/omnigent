export interface ExtensionPage {
  id: string;
  title: string;
  route: string;
  view: string;
}

export interface ExtensionPrimaryNavigation {
  id: string;
  label: string;
  page: string;
  icon: string | null;
  order: number;
  when: string | null;
}

export type ExtensionSlotId =
  "chat.header.actions" | "composer.actions" | "session.rightRail.tabs" | "settings.sections";

export interface ExtensionSlotItem {
  id: string;
  slot: ExtensionSlotId;
  kind: "action" | "tab" | "section";
  label: string;
  page: string;
  icon: string | null;
  order: number;
  when: string | null;
}

export interface ExtensionTool {
  id: string;
  tool_name: string;
  title: string;
  description: string;
  input_schema: Record<string, unknown>;
  runner_permissions: string[];
  enablement: "deployment" | "user" | "agent" | "session";
  is_async: boolean;
}

export interface ExtensionBrowserBundle {
  declared: boolean;
  has_styles: boolean;
  digest: string | null;
  script_url: string | null;
  style_url: string | null;
}

export interface ExtensionCatalogItem {
  object: "extension";
  id: string;
  display_name: string;
  distribution: string;
  version: string;
  extension_api: number;
  status: "enabled" | "unavailable";
  permissions: string[];
  pages: ExtensionPage[];
  primary_navigation: ExtensionPrimaryNavigation[];
  slot_items: ExtensionSlotItem[];
  tools: ExtensionTool[];
  browser: ExtensionBrowserBundle;
}

export interface ExtensionCatalogResponse {
  object: "list";
  data: ExtensionCatalogItem[];
}

export interface ResolvedExtensionPage {
  extension: ExtensionCatalogItem;
  page: ExtensionPage;
}
