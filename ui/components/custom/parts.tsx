"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { CustomSpec, CustomSummary, CustomTestResult, CustomTestRun } from "@/lib/api";
import { CheckIcon, CloseIcon } from "../icons";

/** Pieces shared by the builder's agent page and the approver's page, so both describe a
 *  custom agent, its release and its tests in the same words. */

const PILL: Record<string, React.CSSProperties> = {
  draft: { background: "var(--bubble)", color: "var(--ink-dim)" },
  waiting: { background: "var(--warn-bg)", color: "var(--ink)" },
  live: { background: "color-mix(in srgb, var(--ok) 14%, transparent)", color: "var(--ok)" },
  replaced: { background: "var(--bubble)", color: "var(--ink-faint)" },
  retired: { background: "var(--bubble)", color: "var(--ink-faint)" },
};

export function ReleasePill({ status, label }: { status: string; label: string }) {
  return (
    <span className="status-pill whitespace-nowrap" style={PILL[status] || PILL.draft}>
      {label}
    </span>
  );
}

/** Where a version is in its release: built, tested, approved, live. Words and a tick, never colour alone. */
export function ReleaseSteps({ s }: { s: CustomSummary }) {
  const st = s.latest.status;
  const m = s.latest.meta || {};
  const tested = !!m.test?.passed;
  const steps = [
    { label: "Built", done: true, note: `Version ${s.latest.version}` },
    {
      label: "Tested",
      done: tested || st === "waiting" || st === "live",
      note: m.test ? `${m.test.good} of ${m.test.total} answers good` : "Not yet",
    },
    {
      label: "Approved",
      done: st === "live" || st === "retired" || st === "replaced",
      note:
        st === "waiting"
          ? `Waiting for ${(m.submitted?.approvers || []).join(", ")}`
          : m.decision && !m.decision.approved
            ? "Sent back"
            : st === "live"
              ? `By ${m.decision?.by || "an approver"}`
              : "Not yet",
    },
    { label: "Live", done: st === "live", note: st === "live" ? "People can use it" : st === "retired" ? "Retired" : "Not yet" },
  ];
  return (
    <ol className="grid gap-2 sm:grid-cols-4" aria-label="Release steps">
      {steps.map((x, i) => (
        <li
          key={x.label}
          className="flex items-center gap-3 rounded-xl px-3 py-2.5"
          style={{ border: "1px solid var(--line)", background: x.done ? "var(--brand-soft)" : "var(--surface)" }}
        >
          <span
            aria-hidden
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold"
            style={x.done ? { background: "var(--brand)", color: "var(--brand-ink)" } : { background: "var(--bubble)", color: "var(--ink-dim)" }}
          >
            {x.done ? <CheckIcon size={14} /> : i + 1}
          </span>
          <span className="min-w-0">
            <span className="block text-[14px] font-semibold">{x.label}</span>
            <span className="block truncate text-[12.5px] muted" title={x.note}>
              {x.done ? "Done · " : ""}
              {x.note}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}

const VERDICT: Record<string, { word: string; style: React.CSSProperties }> = {
  pass: { word: "Good", style: { background: "color-mix(in srgb, var(--ok) 14%, transparent)", color: "var(--ok)" } },
  fail: { word: "Needs attention", style: { background: "var(--err-bg)", color: "var(--err)" } },
  ungraded: { word: "Not sure", style: { background: "var(--warn-bg)", color: "var(--ink)" } },
};

/** Every test question with the agent's real answer and the checker's verdict. A builder may
 *  accept an answer the checker did not pass, with a reason the approvers see. */
export function TestResults({
  run,
  onAccept,
}: {
  run: CustomTestRun;
  onAccept?: (index: number, note: string) => Promise<void>;
}) {
  const good = run.results.filter((r) => r.verdict === "pass" || r.accepted).length;
  return (
    <div>
      <p className="text-[14px]" role="status">
        <b>
          {good} of {run.results.length} answers good
        </b>
        <span className="muted">
          {" "}
          · tested by {run.by} · {run.passed ? "ready to send for approval" : "fix the agent, or accept answers you have checked"}
        </span>
      </p>
      <p className="help">
        Each answer was given by the agent for real (tools that change something are not run in a test) and checked by a different AI
        model. It checks that answers are sensible and on topic, not that every number is right.
      </p>
      <ul className="mt-3 space-y-2">
        {run.results.map((r, i) => (
          <ResultRow key={i} r={r} onAccept={onAccept ? (note) => onAccept(i, note) : undefined} />
        ))}
      </ul>
    </div>
  );
}

function ResultRow({ r, onAccept }: { r: CustomTestResult; onAccept?: (note: string) => Promise<void> }) {
  const [open, setOpen] = useState(r.verdict !== "pass" && !r.accepted);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const v = r.accepted ? { word: "Accepted", style: VERDICT.pass.style } : VERDICT[r.verdict] || VERDICT.ungraded;
  return (
    <li className="rounded-xl" style={{ border: "1px solid var(--line)", background: "var(--surface)" }}>
      <button type="button" className="flex w-full items-start gap-3 px-4 py-3 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="status-pill mt-0.5 whitespace-nowrap" style={v.style}>
          {v.word}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[14px] font-medium">{r.question}</span>
          <span className="block text-[12.5px] muted">
            {r.rule ? `Checks the rule: ${r.rule}` : r.expect ? `Expected: ${r.expect}` : ""}
          </span>
        </span>
        <span className="shrink-0 text-[12px] faint">{r.seconds}s</span>
      </button>
      {open ? (
        <div className="space-y-2 border-t px-4 py-3 text-[13.5px]" style={{ borderColor: "var(--line)" }}>
          <p className="faint text-[12px] font-semibold uppercase tracking-wide">Its answer</p>
          <div className="md relative max-h-72 overflow-auto break-words text-[14px] leading-relaxed">
            {r.answer ? (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // Answers come from an agent: links leave the portal in a new tab.
                  a: ({ node, ...p }) => <a {...p} target="_blank" rel="noopener noreferrer" />,
                  table: ({ node, ...p }) => (
                    <div className="md-table">
                      <table {...p} />
                    </div>
                  ),
                }}
              >
                {r.answer}
              </ReactMarkdown>
            ) : (
              <p className="muted">(no answer)</p>
            )}
          </div>
          {r.tools.length ? <p className="muted">Used: {r.tools.join(", ")}</p> : null}
          <p className="muted">
            <b>Checker:</b> {r.reason || "No reason given."}
          </p>
          {r.accepted ? (
            <p className="rounded-lg px-3 py-2" style={{ background: "var(--brand-soft)" }}>
              <b>Accepted by {r.accepted.by}:</b> {r.accepted.note}
            </p>
          ) : r.verdict !== "pass" && onAccept ? (
            <div className="flex flex-wrap items-end gap-2">
              <label className="min-w-[240px] flex-1">
                <span className="text-[12.5px] font-medium muted">You read it and it is fine? Say why (approvers see this)</span>
                <input className="field mt-1" value={note} onChange={(e) => setNote(e.target.value)} maxLength={400} />
              </label>
              <button
                type="button"
                className="btn btn-quiet"
                disabled={busy || note.trim().length < 3}
                onClick={async () => {
                  setBusy(true);
                  setErr("");
                  try {
                    await onAccept(note.trim());
                  } catch (e: any) {
                    setErr(e.message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {busy ? "Saving…" : "Accept this answer"}
              </button>
              {err ? <span className="w-full text-[12.5px]" style={{ color: "var(--err)" }}>{err}</span> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/** What the agent can use, in words. */
export function Abilities({ spec }: { spec: CustomSpec }) {
  const rows: [string, string][] = [
    ["AI model", spec.model],
    ["Documents", spec.documents.length ? spec.documents.join(", ") : "None"],
    ["Tools", spec.tools.length ? spec.tools.map((t) => t.name).join(", ") : "None"],
    ["Data", spec.data.length ? spec.data.map((d) => d.title).join(", ") : "None"],
    ["Other assistants", spec.assistants.length ? spec.assistants.map((a) => a.name).join(", ") : "None"],
    ["Before a tool changes something", spec.ask_before_changes ? "It asks the person" : "It goes ahead"],
    ["Who will use it", spec.audience.length ? spec.audience.map((a) => a.principal).join(", ") : "Nobody chosen yet"],
  ];
  return (
    <dl className="grid gap-x-6 gap-y-2 text-[14px] sm:grid-cols-[200px_minmax(0,1fr)]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="muted">{k}</dt>
          <dd className="min-w-0 break-words [overflow-wrap:anywhere]">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Rules({ spec }: { spec: CustomSpec }) {
  if (!spec.rules.length) return <p className="text-[14px] muted">No extra rules.</p>;
  return (
    <ul className="space-y-1 text-[14px]">
      {spec.rules.map((r) => (
        <li key={r} className="flex gap-2">
          <span aria-hidden style={{ color: "var(--err)" }}>
            <CloseIcon size={14} />
          </span>
          {r}
        </li>
      ))}
    </ul>
  );
}
