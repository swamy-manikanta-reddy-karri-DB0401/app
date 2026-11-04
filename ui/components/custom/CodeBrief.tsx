"use client";

import { useState } from "react";
import type { CodeSetup, CustomSummary, DesignerModel } from "@/lib/api";
import { modelDetail } from "@/lib/designerModels";
import { Select } from "../bits";
import { PencilIcon } from "../icons";

/** The brief, editable at any step of the code writer, even while the code is being written. Applying it never
 *  starts over when there is code: changed words become edits to the code they affect (the model is sent only
 *  the changed lines), and a change of name, description, model, folders or websites needs no AI at all (those
 *  live in the portal's own config.json), so the code is only checked again. */

export type BriefPlan = { kind: "rewrite" | "edit" | "check" | "none"; words: string };

const lines = (s: string) =>
  s
    .split(/[\n,]/)
    .map((x) => x.trim())
    .filter(Boolean);

export function briefPlan(old: CodeSetup, next: CodeSetup, hasCode: boolean, agentChanged: boolean): BriefPlan {
  const task = next.task.trim() !== old.task.trim();
  const fw = next.framework !== old.framework;
  const other = (["name", "description", "model", "source"] as const).some((k) => next[k] !== old[k]) ||
    next.write_folders.join("\n") !== old.write_folders.join("\n") || next.websites.join("\n") !== old.websites.join("\n");
  if (!task && !fw && !other) return { kind: "none", words: "Nothing has changed." };
  if (!hasCode) return { kind: "rewrite", words: "Nothing has been written yet, so it starts again with the new brief." };
  if (task || (fw && agentChanged))
    return {
      kind: "edit",
      words:
        (task ? "Only the code your changed words affect is edited; the rest stays as it is." : "") +
        (fw && agentChanged ? " agent/agent.py was changed, so the AI moves it to the new framework." : "") +
        (fw && !agentChanged ? " agent/agent.py is swapped for the new framework's; your agent's own code is kept." : ""),
    };
  return {
    kind: "check",
    words: fw
      ? "agent/agent.py is swapped for the new framework's and your agent's own code is kept. No AI needed: it is only checked again."
      : "These live in the portal's own settings file, so the code does not change. No AI needed: it is only checked again.",
  };
}

export function BriefPanel({
  setup,
  models,
  customs,
  writing,
  disabled,
  hasCode,
  agentChanged,
  onApply,
}: {
  setup: CodeSetup;
  models: DesignerModel[];
  customs: CustomSummary[];
  /** The code is being written right now: applying stops it first. */
  writing: boolean;
  disabled?: boolean;
  hasCode: boolean;
  agentChanged: boolean;
  onApply: (next: CodeSetup) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<CodeSetup>(setup);
  const [folders, setFolders] = useState("");
  const [sites, setSites] = useState("");
  const set = (p: Partial<CodeSetup>) => setDraft((d) => ({ ...d, ...p }));
  const next = (): CodeSetup => ({ ...draft, write_folders: lines(folders), websites: lines(sites) });
  const plan = briefPlan(setup, next(), hasCode, agentChanged);
  const start = customs.find((c) => c.id === setup.source)?.name || "Just an AI model";

  if (!open)
    return (
      <div className="card mb-4 flex flex-wrap items-center gap-3 px-5 py-3">
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold">The brief</p>
          <p className="truncate text-[13px] muted" title={setup.task}>
            {setup.name} · {setup.framework === "mlflow" ? "MLflow" : "LangGraph"} · {start} · {setup.task}
          </p>
        </div>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={disabled}
          onClick={() => {
            setDraft(setup);
            setFolders(setup.write_folders.join("\n"));
            setSites(setup.websites.join("\n"));
            setOpen(true);
          }}
        >
          <PencilIcon size={14} /> Edit the brief
        </button>
      </div>
    );

  const chat = models.filter((m) => m.tools !== false);
  return (
    <div className="card mb-4 space-y-4 p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[16px] font-semibold">Edit the brief</h3>
        {writing ? <span className="text-[12.5px] faint">The code is being written; applying stops it first.</span> : null}
      </div>
      <label className="block">
        <span className="label">What it should do</span>
        <textarea className="field mt-1 min-h-[120px]" value={draft.task} maxLength={6000} onChange={(e) => set({ task: e.target.value })} />
      </label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="text-[13px] font-medium muted">Name</span>
          <input className="field mt-1" value={draft.name} maxLength={80} onChange={(e) => set({ name: e.target.value })} />
        </label>
        <label className="block">
          <span className="text-[13px] font-medium muted">What it does (one sentence)</span>
          <input className="field mt-1" value={draft.description} maxLength={300} onChange={(e) => set({ description: e.target.value })} />
        </label>
        <div>
          <span className="text-[13px] font-medium muted">Start from</span>
          <Select
            className="mt-1"
            value={draft.source}
            onChange={(v) => set({ source: v })}
            ariaLabel="Start from"
            options={[{ value: "", label: "Just an AI model" }, ...customs.map((c) => ({ value: c.id, label: c.name }))]}
          />
        </div>
        <div>
          <span className="text-[13px] font-medium muted">AI model the agent uses</span>
          <Select
            className="mt-1"
            value={draft.model}
            onChange={(v) => set({ model: v })}
            ariaLabel="AI model the agent uses"
            placeholder={draft.source ? "The custom agent's own model" : "Choose a model"}
            options={chat.map((m) => ({ value: m.name, label: m.label, detail: modelDetail(m) }))}
          />
        </div>
        <label className="block">
          <span className="text-[13px] font-medium muted">Folders it may save files into</span>
          <textarea className="field mt-1 font-mono text-[13px]" rows={2} value={folders} onChange={(e) => setFolders(e.target.value)} />
        </label>
        <label className="block">
          <span className="text-[13px] font-medium muted">Websites it may call</span>
          <textarea className="field mt-1 font-mono text-[13px]" rows={2} value={sites} onChange={(e) => setSites(e.target.value)} />
        </label>
      </div>
      <div>
        <span className="text-[13px] font-medium muted">How it is built</span>
        <div className="seg mt-1" role="group" aria-label="How it is built">
          <button type="button" aria-pressed={draft.framework === "mlflow"} onClick={() => set({ framework: "mlflow" })}>
            MLflow
          </button>
          <button type="button" aria-pressed={draft.framework === "langgraph"} onClick={() => set({ framework: "langgraph" })}>
            LangGraph
          </button>
        </div>
      </div>
      <p className="rounded-lg px-3 py-2 text-[13.5px]" style={{ background: "var(--bubble)" }} aria-live="polite">
        {plan.words}
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn btn-primary"
          disabled={plan.kind === "none" || draft.name.trim().length < 2 || draft.task.trim().length < 20 || (!draft.source && !draft.model)}
          onClick={() => {
            setOpen(false);
            onApply(next());
          }}
        >
          {writing ? "Stop and apply" : "Apply"}
        </button>
        <button type="button" className="btn btn-quiet" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
