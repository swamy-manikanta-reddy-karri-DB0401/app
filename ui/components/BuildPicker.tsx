"use client";

import type { ReactNode } from "react";
import {
  ArrowRightIcon,
  BookIcon,
  ChartIcon,
  ChevronRightIcon,
  LayersIcon,
  MessageIcon,
  RocketIcon,
  ShieldIcon,
  UploadIcon,
  WandIcon,
} from "./icons";

/** Build > Create assistant: what to build, chosen the way people choose (what it is for, what it uses, an example
 *  of what people will ask it), with the same names, icons and colours as the Build library. Developer options sit
 *  apart so business users never meet code. */

export type PickTarget = "custom" | "assistant" | "docs" | "genie" | "app" | "write" | "upload";
type Use = "documents" | "data" | "tools" | "assistants";

const USES: { key: Use; label: string; icon: ReactNode }[] = [
  { key: "documents", label: "Documents", icon: <BookIcon size={13} /> },
  { key: "data", label: "Tables", icon: <ChartIcon size={13} /> },
  { key: "tools", label: "Tools", icon: <WandIcon size={13} /> },
  { key: "assistants", label: "Other assistants", icon: <LayersIcon size={13} /> },
];

type Kind = {
  target: PickTarget;
  title: string;
  badge?: string;
  icon: ReactNode;
  cls: string;
  line: string;
  uses: Use[];
  example: string;
  runs: string;
};

const KINDS: Kind[] = [
  {
    target: "custom",
    title: "Custom agent",
    badge: "Tested and approved",
    icon: <ShieldIcon size={22} />,
    cls: "kind-agent",
    line: "Follows your own steps and rules, using whatever you give it. Nobody else can use it until it passes its tests and is approved.",
    uses: ["documents", "data", "tools", "assistants"],
    example: "Build a media plan for Acme from this brief, within a 50,000 budget.",
    runs: "Runs in this portal",
  },
  {
    target: "assistant",
    title: "Multi-tool assistant",
    badge: "All-rounder",
    icon: <LayersIcon size={22} />,
    cls: "kind-supervisor",
    line: "Decides for each question which of its tools, data and document experts to use, and combines the answers.",
    uses: ["documents", "data", "tools", "assistants"],
    example: "Which campaigns are under-delivering, and what does the rate card say they cost?",
    runs: "Runs in Databricks",
  },
  {
    target: "docs",
    title: "Document expert",
    icon: <BookIcon size={22} />,
    cls: "kind-knowledge",
    line: "Reads folders of PDFs, Word files and slides, and answers from them, saying where each answer came from.",
    uses: ["documents"],
    example: "What does our contract say about cancelling a campaign early?",
    runs: "Runs in Databricks",
  },
  {
    target: "genie",
    title: "Data assistant",
    icon: <ChartIcon size={22} />,
    cls: "kind-data",
    line: "Answers questions about your tables in plain English, with the numbers and the query it ran.",
    uses: ["data"],
    example: "What were sales by region last quarter, compared with the one before?",
    runs: "Runs in Databricks",
  },
];

export function BuildPicker({
  onPick,
  dev,
  customsOff,
  appsOff,
  hero,
}: {
  onPick: (t: PickTarget) => void;
  /** Developer mode is on for this admin. */
  dev: boolean;
  /** Why custom agents cannot be made here (no tables), if so. */
  customsOff?: string;
  /** Why agents in code cannot be added here, if so. */
  appsOff?: string;
  /** The "Describe it in your own words" panel, shown first when the designer is on. */
  hero?: ReactNode;
}) {
  return (
    <div className="space-y-8">
      {hero}

      <section aria-labelledby="pick-nocode">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h3 id="pick-nocode" className="text-[17px] font-semibold tracking-tight">
            {hero ? "Or choose the kind yourself" : "Choose the kind of assistant"}
          </h3>
          <span className="text-[13px] faint">No code needed. Everything is shown to you before it is created.</span>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {KINDS.map((k) => {
            const off = k.target === "custom" ? customsOff : undefined;
            return (
              <button key={k.target} type="button" className="pick-card" disabled={!!off} onClick={() => onPick(k.target)}>
                <span className="flex items-start justify-between gap-3">
                  <span aria-hidden className={`kind-tile ${k.cls}`}>
                    {k.icon}
                  </span>
                  {k.badge ? <span className="pick-badge">{k.badge}</span> : null}
                </span>
                <span className="mt-4 block text-[18px] font-semibold tracking-[-0.01em]">{k.title}</span>
                <span className="mt-1.5 block text-[14.5px] leading-[1.55] muted">{k.line}</span>
                <span className="mt-4 flex flex-wrap gap-1.5" aria-label={"Uses: " + k.uses.map((u) => USES.find((x) => x.key === u)!.label).join(", ")}>
                  {USES.map((u) => (
                    <span key={u.key} className={"pick-use" + (k.uses.includes(u.key) ? " on" : "")} aria-hidden>
                      {u.icon}
                      {u.label}
                    </span>
                  ))}
                </span>
                <span className="pick-example">
                  <span aria-hidden className="faint">
                    <MessageIcon size={14} />
                  </span>
                  <span>
                    <span className="block text-[11.5px] font-semibold uppercase tracking-wide faint">People ask it</span>
                    <span className="block text-[14px]">&ldquo;{k.example}&rdquo;</span>
                  </span>
                </span>
                <span className="mt-auto flex items-center justify-between gap-3 pt-4 text-[13px]">
                  <span className="faint">{off || k.runs}</span>
                  <span className="pick-go">
                    Start <ArrowRightIcon size={15} />
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </section>


      <section aria-labelledby="pick-dev">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h3 id="pick-dev" className="text-[17px] font-semibold tracking-tight">
            For developers
          </h3>
          <span className="text-[13px] faint">
            {dev ? "Developer mode is on: code is checked, shown and approved before it runs." : "Write or upload code: turn on Developer mode in Build > Settings."}
          </span>
        </div>
        <div className="grid gap-3 lg:grid-cols-3">
          {dev ? (
            <DevRow
              icon={<WandIcon size={18} />}
              title="Write an agent's code with AI"
              badge="New"
              line="Describe it, watch the code being written, edit it, approve, and it is deployed and tested."
              off={appsOff}
              onClick={() => onPick("write")}
            />
          ) : null}
          {dev ? (
            <DevRow
              icon={<UploadIcon size={18} />}
              title="Upload an agent's code"
              line="A project as a ZIP, such as one exported here and changed. Checked, then deployed for you."
              off={appsOff}
              onClick={() => onPick("upload")}
            />
          ) : null}
          <DevRow
            icon={<RocketIcon size={18} />}
            title="Add an agent you already run"
            line="An agent your team runs as its own Databricks App, offered here like any other assistant."
            off={appsOff}
            onClick={() => onPick("app")}
          />
        </div>
      </section>
    </div>
  );
}

function DevRow({ icon, title, badge, line, off, onClick }: { icon: ReactNode; title: string; badge?: string; line: string; off?: string; onClick: () => void }) {
  return (
    <button type="button" className="pick-row" disabled={!!off} onClick={onClick}>
      <span aria-hidden className="kind-tile kind-code !h-10 !w-10">
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2 text-[15px] font-semibold">
          {title}
          {badge ? <span className="pick-badge">{badge}</span> : null}
        </span>
        <span className="mt-0.5 block text-[13px] leading-5 muted">{off || line}</span>
      </span>
      <span aria-hidden className="faint">
        <ChevronRightIcon size={16} />
      </span>
    </button>
  );
}
