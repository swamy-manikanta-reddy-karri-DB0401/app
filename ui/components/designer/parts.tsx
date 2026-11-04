"use client";

import { ReactNode, useLayoutEffect, useRef, useState } from "react";
import type { DesignerDraft, DesignerMessage, DesignerNewTool, DesignerStep } from "@/lib/api";
import { CheckIcon, SearchIcon, SparkleIcon } from "../icons";
import { FileChips, withoutFileLines } from "./Files";

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// --- where you are -----------------------------------------------------------

export type Phase = "describe" | "review" | "create" | "test";

const PHASES: { key: Phase; label: string }[] = [
  { key: "describe", label: "Describe" },
  { key: "review", label: "Review" },
  { key: "create", label: "Create" },
  { key: "test", label: "Test" },
];

/** The four stages, always visible, so nobody wonders what comes next. Not clickable:
 *  moving around happens with the buttons on each screen, which say what they do. */
export function StageBar({ phase, compact }: { phase: Phase; compact?: boolean }) {
  const at = PHASES.findIndex((p) => p.key === phase);
  return (
    <ol className={compact ? "dz-steps dz-steps-sm" : "dz-steps"} aria-label="Where you are">
      {PHASES.map((p, i) => {
        const state = i < at ? "done" : i === at ? "now" : "next";
        return (
          <li key={p.key} className="flex items-center">
            <span className="dz-step" data-state={state} aria-current={state === "now" ? "step" : undefined}>
              <span className="dz-step-dot">{state === "done" ? <CheckIcon size={13} /> : i + 1}</span>
              <span className={state === "now" ? "" : compact ? "hidden 2xl:inline" : "hidden sm:inline"}>{p.label}</span>
              {state === "done" ? <span className="sr-only"> (done)</span> : null}
            </span>
            {i < PHASES.length - 1 ? <span aria-hidden className="dz-step-line" data-done={i < at ? "true" : "false"} /> : null}
          </li>
        );
      })}
    </ol>
  );
}

// --- the conversation ----------------------------------------------------------

/** The designer's mark: a gradient tile with a sparkle, in place of a face. */
export function Mark({ large }: { large?: boolean }) {
  return (
    <span aria-hidden className={large ? "dz-mark dz-mark-lg" : "dz-mark"}>
      <SparkleIcon size={large ? 26 : 16} />
    </span>
  );
}

/** "your tables, your folders and the ready-made tools" */
export function listWords(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
}

/** What you wrote. A long message (a pasted brief) is folded to a few lines, so the
 *  conversation stays readable; "Show more" opens it. */
function Mine({ m }: { m: DesignerMessage }) {
  const words = m.files?.length ? withoutFileLines(m.content) : m.content;
  const box = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [long, setLong] = useState(false);
  useLayoutEffect(() => {
    const el = box.current;
    if (el && !open) setLong(el.scrollHeight > el.clientHeight + 4);
  }, [words, open]);
  return (
    <div className="flex flex-col items-end">
      {words ? (
        <div className="dz-user">
          <div ref={box} className={open ? "" : "dz-clamp"}>
            {words}
          </div>
          {long || open ? (
            <button type="button" className="mt-1.5 text-[13px] font-semibold" style={{ color: "var(--brand-deep)" }} onClick={() => setOpen(!open)}>
              {open ? "Show less" : "Show more"}
            </button>
          ) : null}
        </div>
      ) : null}
      {m.files?.length ? <FileChips files={m.files} mine /> : null}
    </div>
  );
}

export function Bubble({ m }: { m: DesignerMessage }) {
  if (m.role === "user") return <Mine m={m} />;
  return (
    <div className="flex gap-3">
      <Mark />
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="whitespace-pre-wrap break-words text-[15.5px] leading-relaxed">{m.content}</div>
        {m.looked?.length ? (
          <span className="dz-activity mt-2" title="What it checked in your workspace before answering">
            <SearchIcon size={12} />
            <span className="truncate">Checked {listWords(m.looked)}</span>
          </span>
        ) : null}
      </div>
    </div>
  );
}

// --- plain-words descriptions --------------------------------------------------

export const KIND_NAME: Record<string, string> = {
  supervisor: "Combines tools and information",
  genie: "Answers questions from your data",
  knowledge: "Answers questions from your documents",
};

export const TOOL_KIND: Record<string, string> = {
  uc_function: "Data function",
  genie_space: "Data assistant",
  knowledge_assistant: "Document assistant",
  volume: "Folder",
  uc_connection: "Outside tool",
  app: "Tool",
  vector_search_index: "Search index",
};

/** What a proposed tool can reach, in plain words, from the checks made on its code. */
export function reach(n: DesignerNewTool): string[] {
  const r = n.report;
  if (n.kind === "uc_function")
    return n.report?.language === "python"
      ? ["Does a calculation on what it is given (Python)", "No internet and no files: Databricks runs it shut off", "Cannot change anything"]
      : ["Does a calculation or query, as the person using it", "Cannot change anything"];
  const out: string[] = [];
  if (r.reads_files) out.push("Reads files");
  if (r.saves_files) out.push("Saves files");
  if (r.calls_internet) out.push(`Contacts ${r.hosts?.length ? r.hosts.join(", ") : "websites"}`);
  if (r.may_change_outside) out.push("May send changes to another system");
  if (r.settings?.length) out.push(`Uses the connection settings ${r.settings.join(", ")}`);
  for (const f of r.folders || []) out.push(`${f.access === "write" ? "Can save to" : "Can read from"} the folder ${f.volume}`);
  if (!out.length) out.push("Only works on what it is given");
  return out;
}

export function Section({ title, icon, children, flash }: { title: string; icon?: ReactNode; children: ReactNode; flash?: string }) {
  return (
    <div className="mt-5 first:mt-0">
      <h4 className="dz-sec-title">
        {icon ? <span aria-hidden>{icon}</span> : null}
        {title}
      </h4>
      {/* `flash` changes when the content does, which replays a brief highlight: you see what just changed. */}
      <div key={flash} className={flash ? "dz-flash mt-2 text-[14.5px]" : "mt-2 text-[14.5px]"}>
        {children}
      </div>
    </div>
  );
}

// --- keeping a half-finished interview across a refresh -----------------------------

const KEY = "agent-portal-designer";

export type Saved = {
  msgs: DesignerMessage[];
  draft: DesignerDraft;
  ready: boolean;
  multiple: boolean;
  progress: DesignerStep[];
  /** When it was saved (ms). An old conversation is not picked up again. */
  at?: number;
};

/** How long a half-finished interview survives a refresh. Longer, and it is more likely to be a
 *  conversation someone has forgotten than one they are still in the middle of. */
const KEEP_MS = 60 * 60 * 1000;

export function loadSaved(): Saved | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    const s = raw ? (JSON.parse(raw) as Saved) : null;
    if (s && (!s.at || Date.now() - s.at > KEEP_MS)) {
      sessionStorage.removeItem(KEY);
      return null;
    }
    return s;
  } catch {
    return null;
  }
}

export function keepSaved(s: Saved | null) {
  try {
    if (s) sessionStorage.setItem(KEY, JSON.stringify({ ...s, at: Date.now() }));
    else sessionStorage.removeItem(KEY);
  } catch {
    // Private windows can refuse storage; the interview still works.
  }
}
