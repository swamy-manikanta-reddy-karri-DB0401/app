"use client";

import { ReactNode, useEffect, useState } from "react";
import { middleShort } from "@/lib/people";
import { Pager, usePage } from "./bits";
import { BookIcon, ChartIcon, LayersIcon, PlusIcon, RocketIcon, SearchIcon, ShieldIcon } from "./icons";

/** Everything an admin has built, as one calm library.
 *
 *  Modelled on the Agent Store the team liked: underline tabs, one search, a plain heading per
 *  kind with its count, and cards that all read the same way: name, status, a short
 *  description, a few labelled facts, a note when something needs doing, and two clear buttons
 *  (try it, manage it). "All" shows the first six of each kind with "Show all", so the page
 *  stays short. Only facts the portal really has are shown. */

export type Kind = "custom" | "assistant" | "docs" | "data" | "code";
export type Tone = "ok" | "wait" | "warn" | "off";

export type LibItem = {
  key: string;
  kind: Kind;
  title: string;
  description: string;
  status?: { label: string; tone: Tone };
  /** Labelled facts, shown as a small table on the card (only ones that are known). */
  facts?: [string, string][];
  /** Something a person should do about it, in words. */
  note?: string;
  /** Made by Databricks on its own (an app's Genie space): hidden unless searched for. */
  auto?: boolean;
  /** Chat with it now (its name in the assistant list), when it is ready. */
  tryKey?: string;
  manageLabel?: string;
  onOpen: () => void;
};

export type LibGroup = { kind: Kind; items: LibItem[] | null; error?: string; off?: string };

const KINDS: Record<Kind, { label: string; tag: string; note: string; icon: (s?: number) => ReactNode; cls: string }> = {
  custom: {
    label: "Custom agents",
    tag: "Custom agent",
    note: "Run in this portal. Tested and approved before people use them.",
    icon: (s = 18) => <ShieldIcon size={s} />,
    cls: "kind-agent",
  },
  assistant: {
    label: "Multi-tool assistants",
    tag: "Multi-tool assistant",
    note: "Use tools, data and documents together to answer. Built as Supervisor Agents in Databricks.",
    icon: (s = 18) => <LayersIcon size={s} />,
    cls: "kind-supervisor",
  },
  docs: {
    label: "Document experts",
    tag: "Document expert",
    note: "Answer questions from folders of documents.",
    icon: (s = 18) => <BookIcon size={s} />,
    cls: "kind-knowledge",
  },
  data: {
    label: "Data assistants",
    tag: "Data assistant",
    note: "Answer questions about your tables in plain English.",
    icon: (s = 18) => <ChartIcon size={s} />,
    cls: "kind-data",
  },
  code: {
    label: "Built in code",
    tag: "Built in code",
    note: "Agents your developers run as their own apps, offered here.",
    icon: (s = 18) => <RocketIcon size={s} />,
    cls: "kind-code",
  },
};
const ORDER: Kind[] = ["custom", "assistant", "docs", "data", "code"];
const PREVIEW = 6;
const KEY = "agent-portal-build-tab";

const TONE: Record<Tone, React.CSSProperties> = {
  ok: { background: "color-mix(in srgb, var(--ok) 14%, transparent)", color: "var(--ok)" },
  wait: { background: "var(--warn-bg)", color: "var(--ink)" },
  warn: { background: "var(--err-bg)", color: "var(--err)" },
  off: { background: "var(--bubble)", color: "var(--ink-dim)" },
};

type Tab = "all" | Kind;

export function BuildLibrary({ groups, onCreate, onTry }: { groups: LibGroup[]; onCreate: () => void; onTry?: (key: string) => void }) {
  const [tab, setTab] = useState<Tab>("all");
  const [q, setQ] = useState("");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(KEY) as Tab | null;
      if (saved && (saved === "all" || saved in KINDS)) setTab(saved);
    } catch {
      // Storage can be blocked; the page then opens on All.
    }
  }, []);
  function pick(t: Tab) {
    setTab(t);
    try {
      localStorage.setItem(KEY, t);
    } catch {
      // Only a convenience.
    }
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const needle = q.trim().toLowerCase();
  const view = ORDER.map((kind) => {
    const g = groups.find((x) => x.kind === kind) || { kind, items: [] };
    const all = g.items || [];
    const matching = all.filter((i) => !needle || `${i.title} ${i.description} ${(i.facts || []).map((f) => f[1]).join(" ")}`.toLowerCase().includes(needle));
    const shown = needle ? matching : matching.filter((i) => !i.auto);
    return { ...g, kind, all, shown, hidden: matching.length - shown.length };
  });
  const loading = groups.some((g) => g.items === null);
  const total = view.reduce((n, g) => n + g.shown.length, 0);
  const tabs = view.filter((g) => g.all.some((i) => !i.auto) || g.kind === tab);
  const panels = view.filter((g) => (tab === "all" ? g.shown.length || g.items === null || g.error || g.off || g.hidden : g.kind === tab));

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0 max-w-2xl">
          <h1 className="text-[28px] font-semibold leading-tight tracking-[-0.02em]">Build</h1>
          <p className="mt-1 text-[15px] muted">Create assistants, and change, test or share the ones you have.</p>
        </div>
        <button type="button" className="btn btn-primary shrink-0" onClick={onCreate}>
          <PlusIcon size={16} />
          Create assistant
        </button>
      </div>

      <div className="mb-4 flex gap-1 overflow-x-auto border-b" style={{ borderColor: "var(--line)" }} role="tablist" aria-label="Kinds of assistant">
        <Tab on={tab === "all"} onClick={() => pick("all")} label="All" n={total} />
        {tabs.map((g) => (
          <Tab key={g.kind} on={tab === g.kind} onClick={() => pick(g.kind)} label={KINDS[g.kind].label} n={g.shown.length} />
        ))}
      </div>

      <label className="relative mb-7 block">
        <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 faint" aria-hidden>
          <SearchIcon size={16} />
        </span>
        <input
          type="search"
          className="field !pl-10"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Find an assistant by name or what it does"
          aria-label="Find an assistant"
        />
      </label>

      {panels.map((g) => (
        <Group key={g.kind} g={g} full={tab !== "all" || !!needle} onAll={() => pick(g.kind)} onTry={onTry} searching={!!needle} />
      ))}

      {!loading && total === 0 && (tab === "all" || needle) ? (
        <div className="card px-6 py-12 text-center">
          <p className="text-[15px] font-semibold">{needle ? `Nothing matches “${q.trim()}”` : "Nothing built yet"}</p>
          <p className="mt-1.5 text-sm muted">{needle ? "Try another word." : "Press “Create assistant” to make your first one."}</p>
        </div>
      ) : null}
    </div>
  );
}

function Tab({ on, onClick, label, n }: { on: boolean; onClick: () => void; label: string; n: number }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={on}
      onClick={onClick}
      className="-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2.5 text-[14.5px] font-medium transition"
      style={on ? { borderColor: "var(--brand)", color: "var(--brand-deep)" } : { borderColor: "transparent", color: "var(--ink-dim)" }}
    >
      {label}
      <span className="ml-1.5 text-[12.5px] font-normal faint">{n}</span>
    </button>
  );
}

type Shown = LibGroup & { all: LibItem[]; shown: LibItem[]; hidden: number };

function Group({ g, full, onAll, onTry, searching }: { g: Shown; full: boolean; onAll: () => void; onTry?: (key: string) => void; searching: boolean }) {
  const k = KINDS[g.kind];
  const pg = usePage(g.shown, 24, [g.shown.length, searching]);
  const list = full ? pg.rows : g.shown.slice(0, PREVIEW);
  return (
    <section className="mb-10" aria-label={k.label}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2 border-b pb-3" style={{ borderColor: "var(--line)" }}>
        <div className="min-w-0">
          <h2 className="text-[17px] font-semibold tracking-[-0.01em]">
            {k.label}
            <span className="ml-2 text-[13px] font-normal faint">{g.items === null ? "" : g.shown.length}</span>
          </h2>
          <p className="mt-0.5 text-[13.5px] muted">{k.note}</p>
        </div>
        {!full && g.shown.length > PREVIEW ? (
          <button type="button" className="text-[14px] font-medium" style={{ color: "var(--brand-deep)" }} onClick={onAll}>
            Show all {g.shown.length} →
          </button>
        ) : null}
      </div>
      {g.error ? <p className="help">Could not list them ({g.error.slice(0, 160)}).</p> : null}
      {g.off ? <p className="text-sm muted">{g.off}</p> : null}
      {g.items === null ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(min(320px,100%),1fr))] gap-4" aria-busy>
          {[0, 1, 2].map((n) => (
            <div key={n} className="card h-[220px] animate-pulse" />
          ))}
        </div>
      ) : list.length ? (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(min(320px,100%),1fr))] gap-4">
          {list.map((i) => (
            <li key={i.key} className="min-w-0">
              <Card i={i} onTry={onTry} />
            </li>
          ))}
        </ul>
      ) : !g.off && !g.error && !g.hidden ? (
        <p className="text-sm muted">{searching ? "Nothing here matches." : "None yet."}</p>
      ) : null}
      {g.hidden ? (
        <p className="mt-3 text-[13px] faint">
          {g.hidden} more {g.hidden === 1 ? "was" : "were"} created automatically by an app and {g.hidden === 1 ? "is" : "are"} not listed. Search to find{" "}
          {g.hidden === 1 ? "it" : "them"}.
        </p>
      ) : null}
      {full ? (
        <div className="mt-4">
          <Pager pg={pg} noun={k.label.toLowerCase()} />
        </div>
      ) : null}
    </section>
  );
}

function Card({ i, onTry }: { i: LibItem; onTry?: (key: string) => void }) {
  const k = KINDS[i.kind];
  const [more, setMore] = useState(false);
  const long = (i.description || "").length > 150;
  return (
    <article className="card flex h-full flex-col p-5">
      <header className="flex items-start gap-3">
        <span className={`kind-tile !h-10 !w-10 !rounded-xl ${k.cls}`} aria-hidden>
          {k.icon(19)}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="break-words text-[16px] font-semibold leading-snug" title={i.title}>
            {middleShort(i.title, 70) || "Untitled"}
          </h3>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {i.status ? (
              <span className="status-pill whitespace-nowrap" style={TONE[i.status.tone]}>
                {i.status.label}
              </span>
            ) : null}
            <span className="rounded-md px-2 py-0.5 text-[12px]" style={{ border: "1px solid var(--line)", color: "var(--ink-dim)" }}>
              {k.tag}
            </span>
          </div>
        </div>
      </header>

      <p className={`mt-3 text-[14px] leading-relaxed ${i.description ? "muted" : "faint"} ${more ? "" : "line-clamp-3"}`}>
        {i.description || "No description yet."}
      </p>
      {long ? (
        <button type="button" className="mt-0.5 self-start text-[13px] font-medium" style={{ color: "var(--brand-deep)" }} onClick={() => setMore(!more)}>
          {more ? "Show less" : "Show more"}
        </button>
      ) : null}

      {i.facts?.length ? (
        <dl className="mt-4 grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
          {i.facts.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="faint">{label}</dt>
              <dd className="min-w-0 truncate" title={value}>
                {value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      {i.note ? (
        <p className="mt-4 rounded-r-md py-2 pl-3 pr-2 text-[13px]" style={{ borderLeft: "3px solid var(--brand)", background: "var(--canvas)" }}>
          {i.note}
        </p>
      ) : null}

      <footer className="mt-auto flex flex-wrap gap-2 pt-5">
        {i.tryKey && onTry ? (
          <button type="button" className="btn btn-primary !min-h-[38px] !px-4" onClick={() => onTry(i.tryKey!)}>
            Try it
          </button>
        ) : null}
        <button type="button" className="btn btn-quiet !min-h-[38px] !px-4" onClick={i.onOpen}>
          {i.manageLabel || "Manage"}
        </button>
      </footer>
    </article>
  );
}
