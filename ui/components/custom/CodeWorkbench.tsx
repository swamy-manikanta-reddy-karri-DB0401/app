"use client";

import { useEffect, useState } from "react";
import type { CodeCheck, CodeRound, SandboxRun } from "@/lib/api";
import { Diff } from "../designer/ToolCheck";
import { CheckIcon, FileIcon, LockIcon, PencilIcon } from "../icons";
import { CodeEditor, marksFor } from "./CodeEditor";

/** The code writer's workspace (CodeWriter.tsx): the live view while the AI writes (files appear as they are
 *  started, lines as they arrive, then each check and fix round), and the editor after it, checked as you type. */

export const LOCKED = ["app.py", "agent/tools.py", "agent/config.json"];
const LOCK_NOTE: Record<string, string> = {
  "app.py": "Serves the agent and passes on each person's own sign-in.",
  "agent/tools.py": "Every call as the person; tools that change things need approval; saves only into its folders.",
  "agent/config.json": "What it may use: model, tools, data, documents, folders, websites.",
};

type LiveFile = { path: string; body: string; done: boolean };
type LiveEdit = { path: string; search: string; replace: string; done: boolean };

/** Files in a streamed reply (=== FILE path === ... === END FILE ===); the last one may still be arriving. */
export function parseFiles(text: string): LiveFile[] {
  const heads = [...text.matchAll(/=== FILE (\S+) ===\n/g)];
  return heads.map((m, i) => {
    const start = (m.index || 0) + m[0].length;
    const next = i + 1 < heads.length ? heads[i + 1].index || text.length : text.length;
    const end = text.indexOf("\n=== END FILE ===", start);
    const done = end >= 0 && end < next;
    let body = text.slice(start, done ? end : next);
    body = body.replace(/^```(?:python|py)?\n/, "").replace(/\n```\s*$/, "");
    return { path: m[1], body, done };
  });
}

/** SEARCH/REPLACE edits in a streamed reply; the last one may still be arriving. */
export function parseEdits(text: string): LiveEdit[] {
  const out: LiveEdit[] = [];
  for (const block of text.split(/=== EDIT /).slice(1)) {
    const path = block.split(" ")[0];
    const done = block.includes("=== END EDIT ===");
    for (const part of block.split("<<<<<<< SEARCH\n").slice(1)) {
      const [search, rest = ""] = part.split("\n=======\n");
      const replace = rest.split("\n>>>>>>> REPLACE")[0];
      out.push({ path, search, replace, done: done || part.includes(">>>>>>> REPLACE") });
    }
  }
  return out;
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return <span className="tabular-nums">{Math.max(0, Math.round((now - since) / 1000))} s</span>;
}

function FileRow({ path, active, onClick, children }: { path: string; active: boolean; onClick: () => void; children?: React.ReactNode }) {
  return (
    <button type="button" className="file-row" aria-current={active} onClick={onClick} title={path}>
      <span className="faint" aria-hidden>
        <FileIcon size={14} />
      </span>
      <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{path}</span>
      {children}
    </button>
  );
}

function Locked({ active, onPick }: { active: string; onPick: (p: string) => void }) {
  return (
    <>
      <p className="mt-4 px-2 text-[11.5px] font-semibold uppercase tracking-wide faint">The portal&apos;s own</p>
      {LOCKED.map((p) => (
        <FileRow key={p} path={p} active={active === p} onClick={() => onPick(p)}>
          <span className="faint" title="AI cannot change this file" aria-label="Locked">
            <LockIcon size={13} />
          </span>
        </FileRow>
      ))}
    </>
  );
}

/** What the AI is writing, as it writes it. */
export function LiveWriting({
  rounds,
  step,
  started,
  current,
}: {
  rounds: CodeRound[];
  step: string;
  started: number;
  /** The code before this change (for a change asked in words), so the files are there from the start. */
  current: Record<string, string> | null;
}) {
  const writes = rounds.filter((r) => r.kind !== "fix");
  const last = writes[writes.length - 1];
  const files = last ? parseFiles(last.text) : [];
  const shown: LiveFile[] = [
    ...files,
    ...Object.entries(current || {})
      .filter(([p]) => !files.some((f) => f.path === p))
      .map(([path, body]) => ({ path, body, done: true })),
  ];
  const writing = files.find((f) => !f.done);
  const [picked, setPicked] = useState("");
  const path = picked || writing?.path || shown[0]?.path || "agent/logic.py";
  const file = shown.find((f) => f.path === path);
  const linesSoFar = files.reduce((n, f) => n + f.body.split("\n").length, 0);
  const active = rounds[rounds.length - 1];

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b px-5 py-3" style={{ borderColor: "var(--line)" }}>
        <span className="pulse-dot" aria-hidden />
        <b className="text-[14.5px]">{step || "Starting"}…</b>
        <span className="text-[13px] faint">
          <Elapsed since={started} />
          {linesSoFar ? ` · ${linesSoFar} lines written` : ""}
        </span>
        <span className="ml-auto text-[12.5px] faint">Only checked as text. Nothing runs until you approve it.</span>
      </div>
      <div className="grid lg:grid-cols-[240px_minmax(0,1fr)]">
        <div className="border-b p-3 lg:border-b-0 lg:border-r" style={{ borderColor: "var(--line)" }}>
          <p className="px-2 text-[11.5px] font-semibold uppercase tracking-wide faint">Written by AI</p>
          {shown.length ? (
            shown.map((f) => (
              <FileRow key={f.path} path={f.path} active={f.path === path} onClick={() => setPicked(f.path)}>
                {!f.done ? <span className="pulse-dot" aria-label="Being written" /> : <span style={{ color: "var(--ok)" }} aria-label="Written"><CheckIcon size={13} /></span>}
              </FileRow>
            ))
          ) : (
            <p className="px-2 py-2 text-[13px] muted">Waiting for the first file…</p>
          )}
          <Locked active={path} onPick={setPicked} />
        </div>
        <div className="min-w-0 p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2 px-1">
            <span className="font-mono text-[13px] font-semibold">{path}</span>
            {LOCKED.includes(path) ? (
              <span className="text-[12px] faint">{LOCK_NOTE[path]} Shown on the next step.</span>
            ) : (
              <span className="text-[12px] faint">{file ? (file.done ? "written" : "writing…") : "not started"}</span>
            )}
          </div>
          <CodeEditor
            key={"live:" + path}
            value={LOCKED.includes(path) ? "" : file?.body || ""}
            readOnly
            live={!!file && !file.done}
            height={440}
            label={"Code of " + path}
          />
        </div>
      </div>
      <Activity rounds={rounds} active={active} />
    </div>
  );
}

/** Each model reply and what the checks found after it, newest last. */
function Activity({ rounds, active }: { rounds: CodeRound[]; active: CodeRound | undefined }) {
  if (!rounds.length) return null;
  return (
    <div className="border-t px-5 py-3" style={{ borderColor: "var(--line)", background: "var(--code-bg)" }}>
      <p className="text-[11.5px] font-semibold uppercase tracking-wide faint">Activity</p>
      <ol className="mt-2 space-y-2 text-[13px]">
        {rounds.map((r, i) => {
          if (r.kind === "try") return <TryLine key={i} tried={r.tried} running={r === active && r.problems === null} />;
          const running = r === active && r.problems === null;
          const edits = r.kind === "fix" || r.kind === "change" ? parseEdits(r.text) : [];
          const files = r.kind !== "fix" ? parseFiles(r.text) : [];
          return (
            <li key={i}>
              <span className="font-medium">
                {r.kind === "write" ? "Writing the code" : r.kind === "change" ? "Making your change" : "Fixing what the checks found"}
              </span>
              <span className="faint">
                {files.length ? ` · ${files.map((f) => f.path).join(", ")}` : ""}
                {edits.length ? ` · ${edits.length} edit${edits.length === 1 ? "" : "s"}` : ""}
                {running ? " …" : ""}
              </span>
              {edits.length ? (
                <div className="mt-1 space-y-1">
                  {edits.map((e, k) => (
                    <Diff
                      key={k}
                      lines={[
                        `@@ ${e.path}`,
                        ...e.search.split("\n").map((l) => "-" + l),
                        ...e.replace.split("\n").map((l) => "+" + l),
                      ]}
                    />
                  ))}
                </div>
              ) : null}
              {r.problems === null ? null : r.problems.length ? (
                <div className="mt-1 rounded-lg px-3 py-2" style={{ background: "color-mix(in srgb, var(--err) 9%, transparent)" }}>
                  <b>The checks found {r.problems.length}:</b>
                  <ul className="mt-0.5 list-disc pl-5">
                    {r.problems.slice(0, 6).map((p, k) => (
                      <li key={k} className="break-words">
                        {p}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="mt-0.5 flex items-center gap-1.5" style={{ color: "var(--ok)" }}>
                  <CheckIcon size={13} /> Passes the checks
                </p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** One try in the sandbox, in the activity list. */
function TryLine({ tried, running }: { tried?: SandboxRun; running: boolean }) {
  if (running || !tried) return <li className="font-medium">Trying it in the sandbox…</li>;
  if (tried.skipped) return <li className="faint">Not tried in the sandbox: {tried.skipped}</li>;
  const res = tried.results || [];
  const passed = res.filter((x) => x.ok).length;
  return (
    <li>
      <span className="font-medium">Tried in the sandbox</span>
      <span className="faint">
        {" "}
        · {passed} of {res.length} checks passed{tried.seconds !== undefined ? ` in ${tried.seconds} s` : ""}
      </span>
      <SandboxResults tried={tried} only="failed" />
    </li>
  );
}

/** The quick checks' results: each check with what went wrong and where. */
export function SandboxResults({ tried, only }: { tried: SandboxRun; only?: "failed" }) {
  const res = (tried.results || []).filter((x) => (only === "failed" ? x.ok === false : true));
  return (
    <>
      {tried.error ? <p className="mt-1 text-[13px]" style={{ color: "var(--err)" }}>{tried.error}</p> : null}
      {res.length ? (
        <ul className="mt-1 space-y-1">
          {res.map((x) => (
            <li key={x.name} className="flex items-start gap-2 text-[13px]">
              <span
                className="mt-0.5 shrink-0 rounded-full px-1.5 text-[11px] font-bold"
                style={
                  x.ok
                    ? { background: "color-mix(in srgb, var(--ok) 16%, transparent)", color: "var(--ok)" }
                    : x.ok === null
                      ? { background: "var(--bubble)", color: "var(--ink-faint)" }
                      : { background: "color-mix(in srgb, var(--err) 14%, transparent)", color: "var(--err)" }
                }
              >
                {x.ok ? "PASS" : x.ok === null ? "SKIP" : "FAIL"}
              </span>
              <span className="min-w-0 break-words">
                <span className="font-mono text-[12.5px]">{x.name}</span>
                {x.error ? <span className="muted"> {x.error}</span> : null}
                {x.where ? <span className="block font-mono text-[12px] faint">{x.where}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

/** The code after it is written: every AI file editable, the portal's own files readable and locked; checked as
 *  you type, with problem lines marked. */
export function CodeIDE({
  code,
  written,
  fixed,
  check,
  checking,
  onEdit,
}: {
  code: Record<string, string>;
  /** As the AI last wrote it, to mark files you changed by hand. */
  written: Record<string, string>;
  fixed: Record<string, string>;
  check: CodeCheck | null;
  checking: boolean;
  onEdit: (path: string, text: string) => void;
}) {
  const paths = Object.keys(code).sort((a, b) => (a === "agent/logic.py" ? -1 : b === "agent/logic.py" ? 1 : a.localeCompare(b)));
  const [path, setPath] = useState("agent/logic.py");
  const [view, setView] = useState<"code" | "changes">("code");
  const locked = LOCKED.includes(path);
  const problems = check?.problems || [];
  const state = (p: string) => check?.diff.find((d) => d.path === p)?.state;
  const marks = locked ? {} : marksFor(path, problems);
  const diff = check?.diff.find((d) => d.path === path);

  return (
    <div className="card overflow-hidden">
      <div className="grid lg:grid-cols-[240px_minmax(0,1fr)]">
        <div className="border-b p-3 lg:border-b-0 lg:border-r" style={{ borderColor: "var(--line)" }}>
          <p className="px-2 text-[11.5px] font-semibold uppercase tracking-wide faint">Written by AI · yours to edit</p>
          {paths.map((p) => {
            const bad = Object.keys(marksFor(p, problems)).length;
            return (
              <FileRow key={p} path={p} active={p === path} onClick={() => setPath(p)}>
                {code[p] !== written[p] ? (
                  <span style={{ color: "var(--brand-deep)" }} title="Edited by you" aria-label="Edited by you">
                    <PencilIcon size={13} />
                  </span>
                ) : null}
                {bad ? (
                  <span className="rounded-full px-1.5 text-[11px] font-bold" style={{ background: "color-mix(in srgb, var(--err) 16%, transparent)", color: "var(--err)" }}>
                    {bad}
                  </span>
                ) : state(p) === "same" ? (
                  <span className="text-[11px] faint">unchanged</span>
                ) : (
                  <span className="text-[11px] faint">{state(p) || ""}</span>
                )}
              </FileRow>
            );
          })}
          <Locked active={path} onPick={setPath} />
        </div>
        <div className="min-w-0 p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2 px-1">
            <span className="font-mono text-[13px] font-semibold">{path}</span>
            {locked ? (
              <span className="flex items-center gap-1 text-[12px] faint">
                <LockIcon size={12} /> {LOCK_NOTE[path]} AI cannot change it.
              </span>
            ) : (
              <div className="seg ml-auto !p-0.5" role="group" aria-label="Show">
                <button type="button" aria-pressed={view === "code"} onClick={() => setView("code")}>
                  Code
                </button>
                <button type="button" aria-pressed={view === "changes"} onClick={() => setView("changes")}>
                  Changes
                </button>
              </div>
            )}
          </div>
          {locked ? (
            <CodeEditor key={"locked:" + path} value={fixed[path] || ""} readOnly height={520} label={"Code of " + path + " (locked)"} />
          ) : view === "code" ? (
            <CodeEditor key={"edit:" + path} value={code[path] || ""} onChange={(v) => onEdit(path, v)} marks={marks} height={520} label={"Code of " + path} />
          ) : diff && diff.lines.length ? (
            <Diff lines={diff.lines} />
          ) : (
            <p className="px-1 py-6 text-[13.5px] muted">No changes against the project it started from.</p>
          )}
          <div className="mt-2 px-1 text-[13px]" aria-live="polite">
            {checking ? (
              <span className="faint">Checking…</span>
            ) : problems.length ? (
              <div className="rounded-lg px-3 py-2" style={{ background: "color-mix(in srgb, var(--err) 9%, transparent)" }}>
                <b>{problems.length === 1 ? "1 problem" : `${problems.length} problems`} to fix before it can be deployed:</b>
                <ul className="mt-0.5 list-disc pl-5">
                  {problems.map((p, k) => (
                    <li key={k} className="break-words">
                      {p}
                    </li>
                  ))}
                </ul>
              </div>
            ) : check ? (
              <span className="flex items-center gap-1.5" style={{ color: "var(--ok)" }}>
                <CheckIcon size={13} /> Passes the checks
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
