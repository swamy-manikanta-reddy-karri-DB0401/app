"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import type {
  CustomSpec,
  CustomSummary,
  CustomTestResult,
  CustomTestRun,
  SafetyView,
} from "@/lib/api";
import { CheckIcon, CloseIcon } from "../icons";
import { DEFAULT_POLICY, needsChecker, policyLines } from "./Policy";

/** Pieces shared by the builder's agent page and the approver's page, so both describe a
 *  custom agent, its release and its tests in the same words. */

const PILL: Record<string, React.CSSProperties> = {
  draft: { background: "var(--bubble)", color: "var(--ink-dim)" },
  waiting: { background: "var(--warn-bg)", color: "var(--ink)" },
  live: {
    background: "color-mix(in srgb, var(--ok) 14%, transparent)",
    color: "var(--ok)",
  },
  replaced: { background: "var(--bubble)", color: "var(--ink-faint)" },
  retired: { background: "var(--bubble)", color: "var(--ink-faint)" },
};

export function ReleasePill({
  status,
  label,
}: {
  status: string;
  label: string;
}) {
  return (
    <span
      className="status-pill whitespace-nowrap"
      style={PILL[status] || PILL.draft}
    >
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
      note: m.test
        ? `${m.test.good} of ${m.test.total} answers good`
        : "Not yet",
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
    {
      label: "Live",
      done: st === "live",
      note:
        st === "live"
          ? "People can use it"
          : st === "retired"
            ? "Retired"
            : "Not yet",
    },
  ];
  return (
    <ol className="grid gap-2 sm:grid-cols-4" aria-label="Release steps">
      {steps.map((x, i) => (
        <li
          key={x.label}
          className="flex items-center gap-3 rounded-xl px-3 py-2.5"
          style={{
            border: "1px solid var(--line)",
            background: x.done ? "var(--brand-soft)" : "var(--surface)",
          }}
        >
          <span
            aria-hidden
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold"
            style={
              x.done
                ? { background: "var(--brand)", color: "var(--brand-ink)" }
                : { background: "var(--bubble)", color: "var(--ink-dim)" }
            }
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
  pass: {
    word: "Good",
    style: {
      background: "color-mix(in srgb, var(--ok) 14%, transparent)",
      color: "var(--ok)",
    },
  },
  fail: {
    word: "Needs attention",
    style: { background: "var(--err-bg)", color: "var(--err)" },
  },
  ungraded: {
    word: "Not sure",
    style: { background: "var(--warn-bg)", color: "var(--ink)" },
  },
};

/** Every test question with the agent's real answer and the checker's verdict. A builder may
 *  accept an answer the checker did not pass, with a reason the approvers see. */
const SCORE_NAME: Record<string, string> = {
  relevant: "Answers the question",
  complete: "Complete",
  grounded: "Backed by its sources",
  no_made_up: "Nothing made up",
  in_role: "Keeps to its rules",
  safe: "Safe",
};
const scoreName = (k: string) =>
  SCORE_NAME[k] || (k.match(/^c(\d+)$/) ? `Your measure ${k.slice(1)}` : k);

/** Scores 0-1 as small labelled bars; a low one (under the bar) is marked and says why. */
function Scores({
  scores,
  notes = {},
  bar = 0.7,
}: {
  scores: Record<string, number>;
  notes?: Record<string, string>;
  bar?: number;
}) {
  const keys = Object.keys(scores);
  if (!keys.length) return null;
  return (
    <div className="grid gap-x-4 gap-y-1.5 sm:grid-cols-2">
      {keys.map((k) => {
        const v = scores[k];
        const low = v < bar;
        return (
          <div key={k} className="min-w-0">
            <div className="flex items-center justify-between gap-2 text-[12.5px]">
              <span className="truncate">{scoreName(k)}</span>
              <span
                className="tabular-nums"
                style={
                  low ? { color: "var(--err)", fontWeight: 600 } : undefined
                }
              >
                {v.toFixed(2)}
              </span>
            </div>
            <div className="progress mt-0.5 !h-1.5" aria-hidden>
              <span
                style={{
                  width: Math.round(v * 100) + "%",
                  ...(low ? { background: "var(--err)" } : {}),
                }}
              />
            </div>
            {low && notes[k] ? (
              <p className="mt-0.5 text-[12px] muted">{notes[k]}</p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function TestResults({
  run,
  onAccept,
}: {
  run: CustomTestRun;
  onAccept?: (index: number, note: string) => Promise<void>;
}) {
  const good = run.results.filter(
    (r) => r.verdict === "pass" || r.accepted,
  ).length;
  return (
    <div>
      <p className="text-[14px]" role="status">
        <b>
          {good} of {run.results.length} answers good
        </b>
        <span className="muted">
          {" "}
          · tested by {run.by} ·{" "}
          {run.passed
            ? "ready to send for approval"
            : "fix the agent, or accept answers you have checked"}
        </span>
      </p>
      <p className="help">
        Each answer was given by the agent for real (tools that change something
        are not run in a test) and checked by a different AI model. It checks
        that answers are sensible and on topic, not that every number is right.
      </p>
      {(() => {
        // The average of each score across the run, for a quick read of where it is weak.
        const sums: Record<string, number[]> = {};
        run.results.forEach((r) =>
          Object.entries(r.scores || {}).forEach(
            ([k, v]) => (sums[k] = [...(sums[k] || []), v]),
          ),
        );
        const avg = Object.fromEntries(
          Object.entries(sums).map(([k, vs]) => [
            k,
            vs.reduce((a, b) => a + b, 0) / vs.length,
          ]),
        );
        return Object.keys(avg).length ? (
          <div
            className="mt-3 rounded-xl p-3"
            style={{ background: "var(--bubble)" }}
          >
            <p className="mb-2 text-[12.5px] font-semibold uppercase tracking-wide faint">
              Average scores
            </p>
            <Scores scores={avg} />
          </div>
        ) : null;
      })()}
      <ul className="mt-3 space-y-2">
        {run.results.map((r, i) => (
          <ResultRow
            key={i}
            r={r}
            onAccept={onAccept ? (note) => onAccept(i, note) : undefined}
          />
        ))}
      </ul>
    </div>
  );
}

function ResultRow({
  r,
  onAccept,
}: {
  r: CustomTestResult;
  onAccept?: (note: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(r.verdict !== "pass" && !r.accepted);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const v = r.accepted
    ? { word: "Accepted", style: VERDICT.pass.style }
    : VERDICT[r.verdict] || VERDICT.ungraded;
  return (
    <li
      className="rounded-xl"
      style={{ border: "1px solid var(--line)", background: "var(--surface)" }}
    >
      <button
        type="button"
        className="flex w-full items-start gap-3 px-4 py-3 text-left"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="status-pill mt-0.5 whitespace-nowrap" style={v.style}>
          {v.word}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[14px] font-medium">{r.question}</span>
          {r.persona ? (
            <span className="block text-[12.5px]">
              <span className="faint">A pretend person: </span>
              {[r.persona.who, r.persona.goal]
                .filter(Boolean)
                .join(", who wants ")}
              {r.conversation ? (
                <span className="faint">
                  {" "}
                  · {
                    r.conversation.filter((t) => t.who === "person").length
                  }{" "}
                  messages
                </span>
              ) : null}
            </span>
          ) : null}
          {r.rerun ? (
            <span className="block text-[12px] faint">
              Re-checked after the last run
            </span>
          ) : null}
          {(r.then || []).map((f, k) => (
            <span key={k} className="block text-[13px]">
              <span className="faint">then: </span>
              {f}
            </span>
          ))}
          <span className="block text-[12.5px] muted">
            {r.rule
              ? `Checks the rule: ${r.rule}`
              : r.expect
                ? `Expected: ${r.expect}`
                : ""}
          </span>
        </span>
        <span className="shrink-0 text-[12px] faint">{r.seconds}s</span>
      </button>
      {open ? (
        <div
          className="space-y-2 border-t px-4 py-3 text-[13.5px]"
          style={{ borderColor: "var(--line)" }}
        >
          {r.conversation && r.conversation.length > 2 ? (
            <details>
              <summary
                className="cursor-pointer text-[13px] font-medium"
                style={{ color: "var(--brand-deep)" }}
              >
                The whole conversation ({r.conversation.length} turns)
              </summary>
              <ol className="mt-2 space-y-1.5">
                {r.conversation.map((t, k) => (
                  <li
                    key={k}
                    className="rounded-lg px-3 py-2 text-[13px]"
                    style={{
                      background:
                        t.who === "person"
                          ? "var(--brand-soft)"
                          : "var(--bubble)",
                    }}
                  >
                    <span className="font-semibold">
                      {t.who === "person" ? "Person" : "Agent"}:{" "}
                    </span>
                    <span className="whitespace-pre-wrap break-words">
                      {t.said}
                    </span>
                  </li>
                ))}
              </ol>
            </details>
          ) : null}
          <p className="faint text-[12px] font-semibold uppercase tracking-wide">
            Its {r.conversation && r.conversation.length > 2 ? "last " : ""}
            answer
          </p>
          <div className="md relative max-h-72 overflow-auto break-words text-[14px] leading-relaxed">
            {r.answer ? (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // Answers come from an agent: links leave the portal in a new tab.
                  a: ({ node, ...p }) => (
                    <a {...p} target="_blank" rel="noopener noreferrer" />
                  ),
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
          {r.tools.length ? (
            <p className="muted">Used: {r.tools.join(", ")}</p>
          ) : null}
          <p className="muted">
            <b>Checker:</b> {r.reason || "No reason given."}
          </p>
          {r.scores && Object.keys(r.scores).length ? (
            <Scores scores={r.scores} notes={r.notes} />
          ) : null}
          {r.accepted ? (
            <p
              className="rounded-lg px-3 py-2"
              style={{ background: "var(--brand-soft)" }}
            >
              <b>Accepted by {r.accepted.by}:</b> {r.accepted.note}
            </p>
          ) : r.verdict !== "pass" && onAccept ? (
            <div className="flex flex-wrap items-end gap-2">
              <label className="min-w-[240px] flex-1">
                <span className="text-[12.5px] font-medium muted">
                  You read it and it is fine? Say why (approvers see this)
                </span>
                <input
                  className="field mt-1"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  maxLength={400}
                />
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
              {err ? (
                <span
                  className="w-full text-[12.5px]"
                  style={{ color: "var(--err)" }}
                >
                  {err}
                </span>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/** What the agent can use, in words. */
export function Abilities({ spec }: { spec: CustomSpec }) {
  const lim = spec.limits;
  const pol = policyLines(spec.policy || DEFAULT_POLICY);
  const rows: [string, string][] = [
    ["AI model", spec.model],
    [
      "Runbook",
      spec.runbook
        ? `${spec.runbook.name} (${spec.runbook.text.length.toLocaleString()} characters${spec.runbook.sha ? ", fingerprint " + spec.runbook.sha.slice(0, 8) : ""})`
        : "None",
    ],
    ["Documents", spec.documents.length ? spec.documents.join(", ") : "None"],
    [
      "Tools",
      spec.tools.length ? spec.tools.map((t) => t.name).join(", ") : "None",
    ],
    [
      "Data",
      spec.data.length ? spec.data.map((d) => d.title).join(", ") : "None",
    ],
    [
      "Other assistants",
      spec.assistants.length
        ? spec.assistants.map((a) => a.name).join(", ")
        : "None",
    ],
    [
      "Before a tool changes something",
      spec.ask_before_changes ? "It asks the person" : "It goes ahead",
    ],
    [
      "Memory",
      spec.memory?.on
        ? "Remembers each person between chats (they can see and clear it)"
        : "Off",
    ],
    [
      "Who will use it",
      spec.audience.length
        ? spec.audience.map((a) => a.principal).join(", ")
        : "Nobody chosen yet",
    ],
    [
      "Each answer",
      lim
        ? `Up to ${lim.steps} steps, replies up to ${lim.reply_tokens.toLocaleString()} tokens`
        : "Default",
    ],
    ["Safety policy", (spec.policy || DEFAULT_POLICY).name],
    ...pol.map(
      ([k, v]) =>
        [k === "Usage limits" ? k : "Safety: " + k.toLowerCase(), v] as [
          string,
          string,
        ],
    ),
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
  if (!spec.rules.length)
    return <p className="text-[14px] muted">No extra rules.</p>;
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

/** The runbook, readable in full: approvers see exactly the procedure they approve. */
export function RunbookView({ spec }: { spec: CustomSpec }) {
  if (!spec.runbook)
    return (
      <p className="text-[14px] muted">
        No runbook: it follows its instructions.
      </p>
    );
  return (
    <div>
      <p className="text-[14px]">
        <b>{spec.runbook.name}</b>{" "}
        <span className="muted">
          · {spec.runbook.text.length.toLocaleString()} characters
          {spec.runbook.sha
            ? ` · fingerprint ${spec.runbook.sha.slice(0, 8)}`
            : ""}
        </span>
      </p>
      <details className="mt-2">
        <summary className="cursor-pointer text-[13px] font-medium muted">
          Read the runbook
        </summary>
        <pre
          className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg p-3 text-[12.5px]"
          style={{ background: "var(--canvas)" }}
        >
          {spec.runbook.text}
        </pre>
      </details>
    </div>
  );
}

/** Everything that keeps this agent safe, in one card, the same for builders and approvers. */
export function SafetyCard({
  id,
  approver = false,
}: {
  id: string;
  approver?: boolean;
}) {
  const [v, setV] = useState<SafetyView | null>(null);
  const [err, setErr] = useState("");
  const [asking, setAsking] = useState(false);
  const [pii, setPii] = useState("MASK");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api
      .customSafety(id, approver)
      .then(setV)
      .catch((e) => setErr(e.message));
  }, [id, approver]);

  const [offAsk, setOffAsk] = useState(false);
  async function change(onNow: boolean) {
    if (!v?.guardrails.endpoint) return;
    setBusy(true);
    setErr("");
    try {
      const g = await api.customGuardrails(
        v.guardrails.endpoint,
        onNow,
        onNow ? pii : "NONE",
      );
      setV({ ...v, guardrails: g });
      setAsking(false);
      setOffAsk(false);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const g = v?.guardrails;
  const on = !!(g?.input?.safety && g?.output?.safety);
  const word = (x?: string) =>
    x === "MASK" ? "masked" : x === "BLOCK" ? "blocked" : "not filtered";
  const rows: [string, React.ReactNode, boolean][] = v
    ? [
        [
          "Databricks safety filter",
          g?.known ? (
            <>
              {on ? "On for questions and answers" : "Off"} on {g.endpoint} ·
              personal data {word(g.output?.pii)}
              {on
                ? ""
                : ". Optional: the portal's safety policy below does the checking, and Databricks' filter is not available in every region"}
            </>
          ) : (
            g?.note || "Could not be read"
          ),
          true,
        ],
        [
          "Safety policy",
          `${v.policy.name}: checked by the portal on every message and answer`,
          true,
        ],
        ...policyLines(v.policy).map(([k, text]) => {
          const good =
            k === "Messages"
              ? v.policy.input.attacks !== "off" &&
                v.policy.input.personal_data !== "off"
              : k === "Answers"
                ? v.policy.output.personal_data !== "off"
                : k === "Checked by"
                  ? !!v.policy.checker || !needsChecker(v.policy)
                  : k === "Usage limits"
                    ? !!(
                        v.policy.usage.requests ||
                        v.policy.usage.tokens ||
                        v.policy.usage.cost_usd ||
                        v.policy.usage.per_person_daily
                      )
                    : true;
          return [k, text, good] as [string, React.ReactNode, boolean];
        }),
        ...(v.live_policy &&
        JSON.stringify(v.live_policy) !== JSON.stringify(v.policy)
          ? [
              [
                "Live version",
                `People using it now get the live version's policy (${v.live_policy.name}) until this one is approved`,
                true,
              ] as [string, React.ReactNode, boolean],
            ]
          : []),
        [
          "Documents and tools",
          "What they return is treated as data it never takes orders from",
          true,
        ],
        [
          "Changes",
          v.ask_before_changes
            ? "A tool that changes something waits for the person"
            : "Tools may change things without asking",
          v.ask_before_changes,
        ],
        [
          "Data access",
          "Each person's own Databricks permissions; it cannot see what they cannot",
          true,
        ],
        [
          "Where the AI runs",
          v.residency?.note || "Not known",
          v.residency?.inside_us_eu === true,
        ],
        [
          "Safety tests",
          !v.tests.run
            ? "Not run yet: run a full test"
            : !v.tests.full
              ? "Last run was a quick test: run a full test"
              : !v.tests.total
                ? "The last full test was before the safety checks existed: run a full test"
                : `${v.tests.good} of ${v.tests.total} passed`,
          v.tests.run &&
            v.tests.full &&
            v.tests.total > 0 &&
            v.tests.good === v.tests.total,
        ],
      ]
    : [];
  return (
    <div className="card mt-4 p-5">
      <h3 className="text-[15px] font-semibold">Safety</h3>
      {err ? (
        <p className="mt-2 text-[13px]" style={{ color: "var(--err)" }}>
          {err}
        </p>
      ) : null}
      {!v && !err ? <p className="mt-2 text-[14px] muted">Checking…</p> : null}
      <ul className="mt-2 divide-y" style={{ borderColor: "var(--line)" }}>
        {rows.map(([k, text, good]) => (
          <li key={k} className="flex items-start gap-3 py-2.5 text-[14px]">
            <span
              aria-hidden
              className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
              style={
                good
                  ? {
                      background:
                        "color-mix(in srgb, var(--ok) 16%, transparent)",
                      color: "var(--ok)",
                    }
                  : { background: "var(--warn-bg)", color: "var(--ink)" }
              }
            >
              {good ? <CheckIcon size={12} /> : "!"}
            </span>
            <span className="w-44 shrink-0 font-medium">{k}</span>
            <span className="min-w-0 flex-1 muted">{text}</span>
          </li>
        ))}
      </ul>
      {g?.known && !on && g.can_change ? (
        asking ? (
          <div
            className="mt-3 rounded-xl p-4"
            style={{ background: "var(--warn-bg)" }}
          >
            <p className="text-[14px]">
              This switches on Databricks&apos; safety filter for{" "}
              <b>{g.endpoint}</b>. It applies to{" "}
              <b>everyone in this workspace who uses that model</b>, not only
              this agent.
            </p>
            <label className="mt-3 block max-w-xs">
              <span className="text-[13px] font-medium muted">
                Personal data (names, emails, phone numbers) in questions and
                answers
              </span>
              <span className="mt-1 flex gap-2">
                {[
                  ["NONE", "Leave it"],
                  ["MASK", "Mask it"],
                  ["BLOCK", "Block it"],
                ].map(([val, label]) => (
                  <button
                    key={val}
                    type="button"
                    className="filter-chip"
                    aria-pressed={pii === val}
                    onClick={() => setPii(val)}
                  >
                    {label}
                  </button>
                ))}
              </span>
            </label>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => change(true)}
              >
                {busy
                  ? "Turning on and checking the model still answers…"
                  : "Turn on"}
              </button>
              <button
                type="button"
                className="btn btn-quiet"
                onClick={() => setAsking(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="btn btn-quiet mt-3"
            onClick={() => setAsking(true)}
          >
            Turn on Databricks safety filter
          </button>
        )
      ) : null}
      {g?.known &&
      (on || (g.output?.pii && g.output.pii !== "NONE")) &&
      g.can_change ? (
        offAsk ? (
          <div
            className="mt-3 flex flex-wrap items-center gap-2 rounded-xl p-3"
            style={{ background: "var(--warn-bg)" }}
          >
            <span className="text-[14px]">
              Turn off Databricks&apos; safety and personal-data filters on{" "}
              <b>{g.endpoint}</b> for everyone who uses it? The portal&apos;s
              own protection stays on.
            </span>
            <button
              type="button"
              className="btn btn-quiet"
              disabled={busy}
              onClick={() => change(false)}
            >
              {busy ? "Turning off…" : "Yes, turn off"}
            </button>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => setOffAsk(false)}
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="btn btn-quiet mt-3"
            onClick={() => setOffAsk(true)}
          >
            Turn off Databricks safety filter
          </button>
        )
      ) : null}
    </div>
  );
}
