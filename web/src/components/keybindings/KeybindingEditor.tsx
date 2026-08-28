import { useMemo, useState } from "react";
import {
  ACTIONS_BY_ID,
  ACTION_CATALOG,
  contextsMayOverlap,
  formatKeybinding,
  getKeybindingSnapshot,
  isMacKeyboardPlatform,
  isReservedEscapeSequence,
  keybindingEnvironmentExpression,
  logicalKeyForCode,
  parseKeybinding,
  replaceAllUserKeybindings,
  resetAllUserKeybindings,
  resetUserKeybindingRule,
  resolveEffectiveKeymap,
  serializeKeybinding,
  setUserKeybindingCandidate,
  unbindDefaultKeybinding,
  useKeybindingSnapshot,
  type ActionId,
  type JsonValue,
  type KeybindingConflict,
  type KeybindingMode,
  type KeybindingMutationResult,
  type KeybindingRule,
  type UserKeybindingRule,
} from "@/actions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useIsEmbedded } from "@/lib/embedded";
import { isNativeShell } from "@/lib/nativeBridge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { KeybindingRecorder } from "./KeybindingRecorder";
import { KeybindingRow, type KeybindingState } from "./KeybindingRow";
import { KEYBINDING_MODE_LABELS } from "./KeybindingSequence";

const TEXT_ENTRY_KEYS = new Set([
  "Enter",
  "Tab",
  "Backspace",
  "Delete",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
  "PageUp",
  "PageDown",
]);

const CUSTOMIZABLE_MODES = [
  "global",
  "composer",
  "terminal",
  "fileViewer",
  "filesPanel",
  "terminalsPanel",
  "executionLogs",
  "markdownToc",
] as const satisfies readonly KeybindingMode[];

interface EditorRow {
  id: string;
  domId: string;
  action: ActionId;
  mode: KeybindingMode;
  sequence: KeybindingRule["sequence"] | null;
  state: KeybindingState;
  defaultRule?: KeybindingRule;
  userRule?: UserKeybindingRule;
}

interface EditTarget {
  id?: string;
  action: ActionId;
  mode: KeybindingMode;
  args?: JsonValue;
  defaultRule?: KeybindingRule;
  alternate: boolean;
}

interface PendingSave {
  rule: UserKeybindingRule;
  conflicts: readonly KeybindingConflict[];
  unsafeTextEntry: boolean;
  browserReserved: boolean;
}

function ruleArgs(rule: KeybindingRule | UserKeybindingRule | undefined): JsonValue | undefined {
  return rule && "args" in rule ? (rule.args as JsonValue | undefined) : undefined;
}

function bindingTitle(title: string, rule: KeybindingRule | UserKeybindingRule): string {
  const args = ruleArgs(rule);
  if (
    rule.action === "session.action.openPinned" &&
    args &&
    typeof args === "object" &&
    !Array.isArray(args)
  ) {
    return `${title} · Slot ${Number(args.slot) + 1}`;
  }
  if (
    rule.action === "composer.action.acceptSuggestion" &&
    args &&
    typeof args === "object" &&
    !Array.isArray(args)
  ) {
    return `${title} · ${args.behavior === "attach" ? "Attach" : "Open or attach"}`;
  }
  if (rule.action === "terminal.action.sendSequence") return `${title} · Terminal input`;
  return title;
}

function messageFor(result: KeybindingMutationResult): string | null {
  if (result.ok) return null;
  switch (result.reason) {
    case "invalidRule":
      return "That key combination is not valid.";
    case "unusableRule":
      return "This action and mode cannot use that binding.";
    case "limitReached":
      return "The keybinding limit has been reached. Remove a custom binding first.";
    case "storageUnavailable":
      return "The binding could not be saved in this browser.";
  }
}

function lastUserRulesById(rules: readonly UserKeybindingRule[]): readonly UserKeybindingRule[] {
  const seen = new Set<string>();
  return [...rules]
    .reverse()
    .filter((rule) => {
      if (seen.has(rule.id)) return false;
      seen.add(rule.id);
      return true;
    })
    .reverse();
}

function parsedSequence(source: string | null): KeybindingRule["sequence"] | null {
  if (!source) return null;
  try {
    return parseKeybinding(source);
  } catch {
    return null;
  }
}

function preservePrimaryModifiers(
  sequence: string,
  defaultRule: KeybindingRule | undefined,
): string {
  if (!defaultRule) return sequence;
  const recorded = parseKeybinding(sequence);
  if (recorded.length !== defaultRule.sequence.length) return sequence;
  let changed = false;
  const normalized = recorded.map((stroke, index) => {
    const original = defaultRule.sequence[index]!;
    if (
      original.key.kind !== stroke.key.kind ||
      original.key.value !== stroke.key.value ||
      !original.modifiers.includes("primary") ||
      !stroke.modifiers.includes("mod")
    )
      return stroke;
    const otherOriginal = original.modifiers.filter((modifier) => modifier !== "primary");
    const otherRecorded = stroke.modifiers.filter((modifier) => modifier !== "mod");
    if (otherOriginal.join("+") !== otherRecorded.join("+")) return stroke;
    changed = true;
    return {
      ...stroke,
      modifiers: stroke.modifiers.map((modifier) =>
        modifier === "mod" ? ("primary" as const) : modifier,
      ),
    };
  });
  return changed ? serializeKeybinding(normalized) : sequence;
}

function isBrowserReserved(sequence: KeybindingRule["sequence"]): boolean {
  const stroke = sequence[0];
  if (!stroke) return false;
  const keyValue =
    stroke.key.kind === "key" ? stroke.key.value : logicalKeyForCode(stroke.key.value);
  if (!keyValue) return false;
  if (["F5", "F11", "F12"].includes(keyValue)) return true;
  const primaryModifiers = stroke.modifiers.filter((modifier) =>
    ["mod", "primary", "ctrl", "meta"].includes(modifier),
  );
  return (
    primaryModifiers.length === 1 &&
    stroke.modifiers.length === 1 &&
    ["l", "n", "p", "q", "r", "t", "w"].includes(keyValue)
  );
}

function nextAlternateId(action: ActionId, rules: readonly UserKeybindingRule[]): string {
  const prefix = `user.${action}.`;
  let index = 1;
  while (rules.some((rule) => rule.id === `${prefix}${index}`)) index += 1;
  return `${prefix}${index}`;
}

export function KeybindingEditor() {
  const snapshot = useKeybindingSnapshot();
  const embedded = useIsEmbedded();
  const native = isNativeShell();
  const [query, setQuery] = useState("");
  const [modeFilter, setModeFilter] = useState<"all" | KeybindingMode>("all");
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
  const [confirmResetAll, setConfirmResetAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isMac = isMacKeyboardPlatform();

  const runtimeDefaults = useMemo(() => {
    const environment = keybindingEnvironmentExpression({
      isMac,
      isNativeShell: native,
      isEmbedded: embedded,
    });
    return snapshot.defaultRules.filter(
      (rule) =>
        !isReservedEscapeSequence(rule.sequence) && contextsMayOverlap(rule.when, environment),
    );
  }, [embedded, isMac, native, snapshot.defaultRules]);
  const runtimeDefaultIds = useMemo(
    () => new Set(runtimeDefaults.map((rule) => rule.id)),
    [runtimeDefaults],
  );
  const defaultsById = useMemo(
    () => new Map(snapshot.defaultRules.map((rule) => [rule.id, rule])),
    [snapshot.defaultRules],
  );
  const displayUserRules = useMemo(
    () => lastUserRulesById(snapshot.userRules),
    [snapshot.userRules],
  );
  const effectiveById = useMemo(
    () => new Map(snapshot.effectiveRules.map((rule) => [rule.id, rule])),
    [snapshot.effectiveRules],
  );
  const userById = useMemo(
    () => new Map(displayUserRules.map((rule) => [rule.id, rule])),
    [displayUserRules],
  );

  const rowsByAction = useMemo(() => {
    const rows = new Map<ActionId, EditorRow[]>();
    for (const defaultRule of runtimeDefaults) {
      const userRule = userById.get(defaultRule.id);
      const effective = effectiveById.get(defaultRule.id);
      const targetedUser =
        userRule?.action === defaultRule.action && userRule.mode === defaultRule.mode
          ? userRule
          : undefined;
      const state: KeybindingState =
        targetedUser?.sequence === null
          ? "Unbound"
          : effective?.origin === "user"
            ? "Modified"
            : "Default";
      const row: EditorRow = {
        id: defaultRule.id,
        domId: defaultRule.id,
        action: defaultRule.action,
        mode: defaultRule.mode,
        sequence: effective?.sequence ?? null,
        state,
        defaultRule,
        userRule: targetedUser,
      };
      rows.set(row.action, [...(rows.get(row.action) ?? []), row]);
    }
    for (const userRule of displayUserRules) {
      if (!ACTIONS_BY_ID.has(userRule.action as ActionId)) continue;
      const storedSequence = parsedSequence(userRule.sequence);
      if (storedSequence && isReservedEscapeSequence(storedSequence)) continue;
      const matchingDefault = defaultsById.get(userRule.id);
      const targetsDefault =
        matchingDefault?.action === userRule.action && matchingDefault.mode === userRule.mode;
      if (targetsDefault && runtimeDefaultIds.has(userRule.id)) continue;
      const action = userRule.action as ActionId;
      const candidateEffective = effectiveById.get(userRule.id);
      const effective =
        candidateEffective?.action === userRule.action && candidateEffective.mode === userRule.mode
          ? candidateEffective
          : undefined;
      const inactiveDefault = targetsDefault && !runtimeDefaultIds.has(userRule.id);
      const row: EditorRow = {
        id: userRule.id,
        domId:
          matchingDefault && !targetsDefault
            ? `${userRule.id}:${userRule.action}:${userRule.mode}`
            : userRule.id,
        action,
        mode: userRule.mode,
        sequence: inactiveDefault ? storedSequence : (effective?.sequence ?? storedSequence),
        state: inactiveDefault ? "Dormant" : effective?.origin === "user" ? "Alternate" : "Dormant",
        userRule,
      };
      rows.set(action, [...(rows.get(action) ?? []), row]);
    }
    return rows;
  }, [defaultsById, effectiveById, runtimeDefaultIds, runtimeDefaults, displayUserRules, userById]);

  const visibleUserRuleIds = useMemo(() => {
    const ids = new Set<string>();
    for (const rows of rowsByAction.values()) {
      for (const row of rows) if (row.userRule) ids.add(row.userRule.id);
    }
    return ids;
  }, [rowsByAction]);

  const visibleActions = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return ACTION_CATALOG.flatMap((definition) => {
      const rows = (rowsByAction.get(definition.id) ?? []).filter(
        (row) => modeFilter === "all" || row.mode === modeFilter,
      );
      const searchable = [
        definition.title,
        definition.id,
        ...(definition.keywords ?? []),
        ...rows.flatMap((row) => [
          row.id,
          row.sequence ? formatKeybinding(row.sequence, { isMac }) : "unbound",
        ]),
      ]
        .join(" ")
        .toLowerCase();
      if (normalizedQuery && !searchable.includes(normalizedQuery)) return [];
      if (modeFilter !== "all" && rows.length === 0) return [];
      return [{ definition, rows }];
    });
  }, [isMac, modeFilter, query, rowsByAction]);

  const grouped = useMemo(() => {
    const groups = new Map<string, typeof visibleActions>();
    for (const item of visibleActions) {
      groups.set(item.definition.category, [...(groups.get(item.definition.category) ?? []), item]);
    }
    return [...groups];
  }, [visibleActions]);

  const handleResult = (result: KeybindingMutationResult): boolean => {
    const message = messageFor(result);
    setError(message);
    return result.ok;
  };

  const closeEditing = () => {
    const target = editing;
    setEditing(null);
    queueMicrotask(() => {
      if (!target) return;
      if (target.id) {
        const row = [...document.querySelectorAll<HTMLElement>("[data-binding-id]")].find(
          (element) => element.dataset.bindingId === target.id,
        );
        row?.querySelector<HTMLElement>("button")?.focus();
        return;
      }
      const label = `Add binding for ${ACTIONS_BY_ID.get(target.action)?.title ?? target.action}`;
      [...document.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.getAttribute("aria-label") === label)
        ?.focus();
    });
  };

  const saveRule = (rule: UserKeybindingRule) => {
    if (handleResult(setUserKeybindingCandidate(rule))) {
      closeEditing();
      setPendingSave(null);
    }
  };

  const previewAndSave = (rule: UserKeybindingRule) => {
    const current = getKeybindingSnapshot();
    const prospective = [
      ...current.userRules.filter((candidate) => candidate.id !== rule.id),
      rule,
    ];
    const conflicts = resolveEffectiveKeymap(current.defaultRules, prospective).conflicts.filter(
      (conflict) => conflict.first.id === rule.id || conflict.second.id === rule.id,
    );
    const firstStroke = rule.sequence ? parseKeybinding(rule.sequence)[0] : undefined;
    const target = current.defaultRules.find(
      (candidate) =>
        candidate.id === rule.id &&
        candidate.action === rule.action &&
        candidate.mode === rule.mode,
    );
    const identityOverride =
      target !== undefined &&
      resolveEffectiveKeymap([target], [rule]).rules[0]?.origin === "default";
    const firstKeyValue = firstStroke
      ? firstStroke.key.kind === "key"
        ? firstStroke.key.value
        : logicalKeyForCode(firstStroke.key.value)
      : undefined;
    const textProducingKey =
      firstKeyValue !== undefined &&
      (firstKeyValue === " " ||
        [...firstKeyValue].length === 1 ||
        TEXT_ENTRY_KEYS.has(firstKeyValue));
    const hasProtectiveModifier = firstStroke?.modifiers.some((modifier) => modifier !== "shift");
    const unsafeTextEntry =
      !identityOverride && firstStroke !== undefined && textProducingKey && !hasProtectiveModifier;
    const browserReserved =
      !identityOverride &&
      rule.sequence !== null &&
      isBrowserReserved(parseKeybinding(rule.sequence));
    if (conflicts.length > 0 || unsafeTextEntry || browserReserved) {
      setPendingSave({ rule, conflicts, unsafeTextEntry, browserReserved });
    } else {
      saveRule(rule);
    }
  };

  const completeRecording = (sequence: string) => {
    if (!editing) return;
    const current = getKeybindingSnapshot();
    const id = editing.id ?? nextAlternateId(editing.action, current.userRules);
    previewAndSave({
      id,
      action: editing.action,
      sequence: preservePrimaryModifiers(sequence, editing.defaultRule),
      mode: editing.mode,
      ...(editing.args === undefined ? {} : { args: editing.args }),
    });
  };

  const resetWhere = (predicate: (rule: UserKeybindingRule) => boolean) => {
    const current = getKeybindingSnapshot();
    if (
      handleResult(replaceAllUserKeybindings(current.userRules.filter((rule) => !predicate(rule))))
    ) {
      setEditing(null);
    }
  };

  const editingPanel = editing ? (
    <div className="rounded-lg border border-primary/40 bg-card p-3" data-testid="binding-editor">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-medium">{ACTIONS_BY_ID.get(editing.action)?.title}</span>
        {editing.alternate ? (
          <Select
            value={editing.mode}
            onValueChange={(value) => {
              const mode = value as KeybindingMode;
              const template = getKeybindingSnapshot().defaultRules.find(
                (rule) => rule.action === editing.action && rule.mode === mode,
              );
              setEditing({
                ...editing,
                mode,
                args: ruleArgs(template) ?? editing.args,
                defaultRule: template,
              });
            }}
          >
            <SelectTrigger data-testid="keybinding-edit-mode" className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CUSTOMIZABLE_MODES.map((mode) => (
                <SelectItem key={mode} value={mode}>
                  {KEYBINDING_MODE_LABELS[mode]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <span className="text-sm text-muted-foreground">
            {KEYBINDING_MODE_LABELS[editing.mode]}
          </span>
        )}
      </div>
      <KeybindingRecorder
        onComplete={completeRecording}
        onCancel={closeEditing}
        label={editing.id ? "Record replacement" : "Record new binding"}
        preferPhysical={editing.defaultRule?.sequence[0]?.key.kind === "code"}
      />
    </div>
  ) : null;

  return (
    <div className="space-y-5" data-testid="keybinding-editor">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label="Search keyboard shortcuts"
          placeholder="Search actions or keys"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="min-w-56 flex-1"
        />
        <Select
          value={modeFilter}
          onValueChange={(value) => setModeFilter(value as "all" | KeybindingMode)}
        >
          <SelectTrigger data-testid="keybinding-mode-filter" className="w-48">
            <SelectValue placeholder="All modes" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All modes</SelectItem>
            {CUSTOMIZABLE_MODES.map((mode) => (
              <SelectItem key={mode} value={mode}>
                {KEYBINDING_MODE_LABELS[mode]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          type="button"
          variant="outline"
          onClick={() =>
            resetWhere(
              (rule) =>
                modeFilter !== "all" &&
                rule.mode === modeFilter &&
                ACTIONS_BY_ID.has(rule.action as ActionId) &&
                visibleUserRuleIds.has(rule.id),
            )
          }
          disabled={modeFilter === "all"}
        >
          Reset current mode
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => setConfirmResetAll(true)}
          disabled={snapshot.userRules.length === 0}
        >
          Reset all
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {grouped.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No keyboard shortcuts found.
        </p>
      ) : (
        grouped.map(([category, actions]) => (
          <section key={category} aria-labelledby={`keybinding-category-${category}`}>
            <h2
              id={`keybinding-category-${category}`}
              className="mb-2 text-sm font-semibold text-muted-foreground"
            >
              {category}
            </h2>
            <div className="space-y-3">
              {actions.map(({ definition, rows }) => {
                const hasOverrides = snapshot.userRules.some(
                  (rule) => rule.action === definition.id,
                );
                const actionHasArguments = snapshot.defaultRules.some(
                  (rule) => rule.action === definition.id && ruleArgs(rule) !== undefined,
                );
                const hasReservedEscapeDefault = snapshot.defaultRules.some(
                  (rule) =>
                    rule.action === definition.id && isReservedEscapeSequence(rule.sequence),
                );
                return (
                  <div key={definition.id} className="rounded-lg border border-border px-3">
                    <div className="flex items-center gap-2 border-b border-border/60 py-2">
                      <span className="flex-1 text-sm font-semibold">{definition.title}</span>
                      {hasOverrides && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => resetWhere((rule) => rule.action === definition.id)}
                        >
                          Reset action
                        </Button>
                      )}
                      {!actionHasArguments && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Add binding for ${definition.title}`}
                          onClick={() => {
                            // Hidden Escape defaults still carry the action's mode/activation policy.
                            const template =
                              rows[0]?.defaultRule ??
                              getKeybindingSnapshot().defaultRules.find(
                                (rule) => rule.action === definition.id,
                              );
                            setEditing({
                              action: definition.id,
                              mode:
                                modeFilter === "all" ? (template?.mode ?? "global") : modeFilter,
                              args: ruleArgs(template),
                              defaultRule: template,
                              alternate: true,
                            });
                          }}
                        >
                          Add binding
                        </Button>
                      )}
                    </div>
                    {editing && !editing.id && editing.action === definition.id && editingPanel}
                    {rows.length === 0 ? (
                      <p className="py-3 text-sm text-muted-foreground">
                        {hasReservedEscapeDefault
                          ? "Escape dismissal is managed automatically."
                          : modeFilter === "all"
                            ? "No default binding."
                            : "No binding in this mode."}
                      </p>
                    ) : (
                      <div>
                        {rows.length > 3 && (
                          <p className="border-b border-border/60 py-2 text-xs text-muted-foreground">
                            {rows.length} context-specific bindings
                          </p>
                        )}
                        <ul
                          className={rows.length > 3 ? "max-h-80 overflow-y-auto pr-1" : undefined}
                        >
                          {rows.map((row) => (
                            <KeybindingRow
                              key={row.domId}
                              title={bindingTitle(
                                definition.title,
                                row.defaultRule ?? row.userRule!,
                              )}
                              actionId={definition.id}
                              ruleId={row.id}
                              domId={row.domId}
                              mode={row.mode}
                              state={row.state}
                              sequence={row.sequence}
                              editor={editing?.id === row.id ? editingPanel : undefined}
                              onEdit={
                                row.state === "Dormant"
                                  ? undefined
                                  : () =>
                                      setEditing({
                                        id: row.id,
                                        action: row.action,
                                        mode: row.mode,
                                        args: ruleArgs(row.userRule) ?? ruleArgs(row.defaultRule),
                                        defaultRule: row.defaultRule,
                                        alternate: !row.defaultRule,
                                      })
                              }
                              onAddAlternate={
                                row.defaultRule
                                  ? () =>
                                      setEditing({
                                        action: row.action,
                                        mode: row.mode,
                                        args: ruleArgs(row.defaultRule),
                                        defaultRule: row.defaultRule,
                                        alternate: true,
                                      })
                                  : undefined
                              }
                              onUnbind={
                                row.defaultRule && row.state !== "Unbound"
                                  ? () => handleResult(unbindDefaultKeybinding(row.defaultRule!))
                                  : undefined
                              }
                              onReset={
                                row.defaultRule && row.state !== "Default"
                                  ? () => handleResult(resetUserKeybindingRule(row.id))
                                  : undefined
                              }
                              onRemove={
                                !row.defaultRule
                                  ? () => handleResult(resetUserKeybindingRule(row.id))
                                  : undefined
                              }
                            />
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        ))
      )}

      <Dialog open={confirmResetAll} onOpenChange={setConfirmResetAll}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset all keyboard shortcuts?</DialogTitle>
            <DialogDescription>
              This removes every saved override, including dormant bindings for future actions.
            </DialogDescription>
          </DialogHeader>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmResetAll(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => {
                if (handleResult(resetAllUserKeybindings())) setConfirmResetAll(false);
              }}
            >
              Reset all bindings
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={pendingSave !== null} onOpenChange={(open) => !open && setPendingSave(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {pendingSave?.conflicts.length
                ? "Conflicting keyboard shortcut"
                : "Review keyboard shortcut"}
            </DialogTitle>
            <DialogDescription>
              This binding may interfere with typing or another action. You can save it
              intentionally after reviewing the behavior below.
            </DialogDescription>
          </DialogHeader>
          <ul className="space-y-2 text-sm" aria-live="polite">
            {pendingSave?.unsafeTextEntry && (
              <li className="rounded bg-muted p-2">
                A text-entry shortcut without Ctrl, Alt, Command, or Meta intercepts that character
                while typing in its mode.
              </li>
            )}
            {pendingSave?.browserReserved && (
              <li className="rounded bg-muted p-2">
                The browser or operating system may reserve this shortcut before Omnigent can
                receive it.
              </li>
            )}
            {pendingSave?.conflicts.map((conflict) => {
              const other =
                conflict.first.id === pendingSave.rule.id ? conflict.second : conflict.first;
              const otherTitle = ACTIONS_BY_ID.get(other.action)?.title ?? other.action;
              const sequenceLabel = formatKeybinding(parseKeybinding(conflict.sequence), {
                isMac,
              });
              const outcome =
                conflict.kind === "chordPrefix"
                  ? `The chord prefix ${sequenceLabel} takes precedence, so the single-stroke action does not run when both contexts match.`
                  : conflict.resolution === "ambiguous"
                    ? `${ACTIONS_BY_ID.get(conflict.winner.action)?.title ?? conflict.winner.action} takes precedence when both contexts are equally active.`
                    : "The action in the focused context wins.";
              return (
                <li
                  key={`${conflict.kind}-${conflict.sequence}-${conflict.first.id}-${conflict.second.id}`}
                  className="rounded bg-muted p-2"
                >
                  Conflicts with <strong>{otherTitle}</strong> ({KEYBINDING_MODE_LABELS[other.mode]}
                  ). {outcome}
                </li>
              );
            })}
          </ul>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setPendingSave(null)}>
              Cancel
            </Button>
            <Button type="button" onClick={() => pendingSave && saveRule(pendingSave.rule)}>
              Save anyway
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
