"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import type { DesignerDraft, DraftFile } from "@/lib/api";
import { Spinner } from "../bits";
import { CopyIcon, DownloadIcon, FileIcon } from "../icons";

/** The side panel's Plan, Files and Changes tabs: the draft as the files a person can read, compare and
 *  download (an idea from Mia Studio). The files are made by the portal from the draft the page already
 *  holds (`designer_files.py`); no model is asked and nothing is stored or run. Changes are against the
 *  draft before the last reply. */

export type PanelTab = "summary" | "plan" | "files" | "changes";

export function useDraftFiles(draft: DesignerDraft) {
  const [files, setFiles] = useState<DraftFile[] | null>(null);
  const [err, setErr] = useState("");
  const last = useRef<{ json: string; draft: DesignerDraft } | null>(null);
  const before = useRef<DesignerDraft | undefined>(undefined);
  const json = useMemo(() => JSON.stringify(draft), [draft]);
  useEffect(() => {
    if (!draft.kind && !draft.display_name) {
      setFiles(null);
      return;
    }
    if (last.current && last.current.json !== json)
      before.current = last.current.draft;
    last.current = { json, draft };
    let gone = false;
    const t = setTimeout(() => {
      api
        .designerDraftFiles(draft, before.current)
        .then((r) => {
          if (!gone) {
            setFiles(r.files);
            setErr("");
          }
        })
        .catch((e) => !gone && setErr(e.message));
    }, 500);
    return () => {
      gone = true;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [json]);
  const changed = (files || []).filter((f) => f.change !== "same").length;
  return { files, err, changed };
}

export function PanelTabs({
  tab,
  onTab,
  changed,
  open,
}: {
  tab: PanelTab;
  onTab: (t: PanelTab) => void;
  changed: number;
  open: number;
}) {
  const tabs: [PanelTab, string, number][] = [
    ["summary", "Summary", 0],
    ["plan", "Plan", open],
    ["files", "Files", 0],
    ["changes", "Changes", changed],
  ];
  return (
    <div
      className="-mb-px mt-3 flex gap-0.5 overflow-x-auto"
      role="tablist"
      aria-label="What to show"
    >
      {tabs.map(([k, label, n]) => (
        <button
          key={k}
          type="button"
          role="tab"
          aria-selected={tab === k}
          onClick={() => onTab(k)}
          className="shrink-0 whitespace-nowrap border-b-2 px-2.5 py-2 text-[13.5px] font-medium transition"
          style={
            tab === k
              ? { borderColor: "var(--brand)", color: "var(--brand-deep)" }
              : { borderColor: "transparent", color: "var(--ink-dim)" }
          }
        >
          {label}
          {n ? (
            <span
              className="ml-1.5 inline-flex min-w-[18px] items-center justify-center rounded-full px-1 text-[11px] font-semibold"
              style={
                k === "plan"
                  ? { background: "var(--warn-bg)", color: "var(--warn-line)" }
                  : {
                      background: "var(--brand-soft)",
                      color: "var(--brand-deep)",
                    }
              }
              title={
                k === "plan"
                  ? `${n} open question${n === 1 ? "" : "s"}`
                  : `${n} changed in the last reply`
              }
            >
              {n}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  );
}

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function slug(draft: DesignerDraft) {
  return (
    (draft.display_name || "assistant")
      .replace(/[^A-Za-z0-9_-]+/g, "-")
      .replace(/^-|-$/g, "") || "assistant"
  );
}

const CHANGE: Record<string, { label: string; style: React.CSSProperties }> = {
  new: {
    label: "New",
    style: { background: "var(--brand-soft)", color: "var(--brand-deep)" },
  },
  changed: {
    label: "Changed",
    style: { background: "var(--warn-bg)", color: "var(--warn-line)" },
  },
  removed: {
    label: "Removed",
    style: { background: "var(--err-bg)", color: "var(--err)" },
  },
};

function ChangeTag({ change }: { change: string }) {
  const c = CHANGE[change];
  return c ? (
    <span
      className="shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold"
      style={c.style}
    >
      {c.label}
    </span>
  ) : null;
}

export function DraftPanel({
  tab,
  draft,
  files,
  err,
}: {
  tab: Exclude<PanelTab, "summary">;
  draft: DesignerDraft;
  files: DraftFile[] | null;
  err: string;
}) {
  const [open, setOpen] = useState("");
  const [zipping, setZipping] = useState(false);
  const [zipErr, setZipErr] = useState("");
  const [copied, setCopied] = useState(false);
  if (!draft.kind && !draft.display_name) {
    return (
      <p className="text-[13.5px] muted">
        The plan and the files appear here as you answer.
      </p>
    );
  }
  if (err && !files) return <p className="error text-[13.5px]">{err}</p>;
  if (!files) return <Spinner label="Writing the files…" />;
  const plan = files.find((f) => f.path === "plan.md");

  async function zip() {
    setZipping(true);
    setZipErr("");
    try {
      save(await api.designerDraftZip(draft), slug(draft) + ".zip");
    } catch (e: any) {
      setZipErr(e.message);
    } finally {
      setZipping(false);
    }
  }

  if (tab === "plan") {
    const questions = draft.open_questions || [];
    return (
      <div>
        {questions.length ? (
          <div className="notice mb-4 !py-2.5 text-[13.5px]">
            <p className="font-medium">
              {questions.length} open question
              {questions.length === 1 ? "" : "s"}: it can be built once{" "}
              {questions.length === 1 ? "it is" : "they are"} answered.
            </p>
            <ul className="mt-1 list-disc pl-5">
              {questions.map((q) => (
                <li key={q} className="break-words">
                  {q}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="md break-words text-[14px] leading-relaxed">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
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
            {plan?.text || ""}
          </ReactMarkdown>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            className="btn btn-quiet !min-h-[34px] !px-3"
            onClick={() =>
              plan &&
              save(
                new Blob([plan.text], { type: "text/markdown" }),
                slug(draft) + "-plan.md",
              )
            }
          >
            <DownloadIcon size={14} /> Download the plan
          </button>
        </div>
        <p className="mt-2 text-[12.5px] faint">
          Approving and building means you agree with this plan. Ask for a
          change in the chat at any time.
        </p>
      </div>
    );
  }

  if (tab === "files") {
    const shown = files.filter((f) => f.change !== "removed");
    const cur = shown.find((f) => f.path === open);
    return (
      <div>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-[13px] muted">
            {shown.length} file{shown.length === 1 ? "" : "s"} so far
          </p>
          <button
            type="button"
            className="btn btn-quiet !min-h-[34px] !px-3"
            onClick={zip}
            disabled={zipping}
          >
            <DownloadIcon size={14} />{" "}
            {zipping ? "Preparing…" : "Download all (ZIP)"}
          </button>
        </div>
        {zipErr ? <p className="error mb-2 text-[13px]">{zipErr}</p> : null}
        <ul
          className="divide-y rounded-xl border divide-[color:var(--line)]"
          style={{ borderColor: "var(--line)" }}
        >
          {shown.map((f) => (
            <li key={f.path}>
              <button
                type="button"
                className="flex w-full min-w-0 items-center gap-2.5 px-3 py-2 text-left text-[13.5px] hover:bg-[var(--bubble)]"
                onClick={() => setOpen(open === f.path ? "" : f.path)}
                aria-expanded={open === f.path}
              >
                <FileIcon size={14} />
                <span
                  className="min-w-0 flex-1 truncate font-mono text-[12.5px]"
                  title={f.path}
                >
                  {f.path}
                </span>
                <span className="shrink-0 text-[11.5px] faint">
                  {f.text.split("\n").length} lines
                </span>
                <ChangeTag change={f.change} />
              </button>
            </li>
          ))}
        </ul>
        {cur ? (
          <div className="mt-3">
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <p
                className="min-w-0 truncate font-mono text-[12.5px]"
                title={cur.path}
              >
                {cur.path}
              </p>
              <button
                type="button"
                className="icon-btn !h-8 !w-8"
                title="Copy"
                aria-label="Copy this file"
                onClick={() => {
                  navigator.clipboard?.writeText(cur.text).then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  });
                }}
              >
                <CopyIcon size={14} />
              </button>
            </div>
            {copied ? <p className="mb-1 text-[12px] faint">Copied</p> : null}
            <pre
              className={`max-h-[420px] overflow-auto rounded-xl p-3 text-[12px] leading-relaxed ${cur.path.endsWith(".md") ? "whitespace-pre-wrap break-words" : ""}`}
              style={{ background: "var(--bubble)" }}
            >
              {cur.text}
            </pre>
            {cur.kind === "tool" ? (
              <p className="mt-1.5 text-[12px] faint">
                Exactly what would be deployed. It is never run in the portal;
                you approve it on the next step.
              </p>
            ) : null}
          </div>
        ) : (
          <p className="mt-2 text-[12.5px] faint">
            Click a file to read it. The files update as the conversation goes.
          </p>
        )}
      </div>
    );
  }

  // Changes since the previous reply
  const changes = files.filter((f) => f.change !== "same");
  if (!changes.length)
    return (
      <p className="text-[13.5px] muted">Nothing changed in the last reply.</p>
    );
  return (
    <div className="space-y-4">
      <p className="text-[13px] muted">
        What the last reply changed. Green lines were added, red lines removed.
      </p>
      {changes.map((f) => (
        <div key={f.path}>
          <div className="mb-1.5 flex min-w-0 items-center gap-2">
            <span
              className="min-w-0 flex-1 truncate font-mono text-[12.5px]"
              title={f.path}
            >
              {f.path}
            </span>
            <ChangeTag change={f.change} />
          </div>
          {f.change === "new" ? (
            <p className="text-[12.5px] faint">
              A new file ({f.text.split("\n").length} lines). Read it on the
              Files tab.
            </p>
          ) : f.change === "removed" ? (
            <p className="text-[12.5px] faint">No longer part of the draft.</p>
          ) : (
            <pre
              className={`max-h-[360px] overflow-auto rounded-xl p-3 text-[12px] leading-relaxed ${f.path.endsWith(".md") ? "whitespace-pre-wrap break-words" : ""}`}
              style={{ background: "var(--bubble)" }}
            >
              {f.diff
                .filter((l) => !l.startsWith("---") && !l.startsWith("+++"))
                .map((l, i) => (
                  <div
                    key={i}
                    style={
                      l.startsWith("+")
                        ? {
                            background:
                              "color-mix(in srgb, var(--ok) 14%, transparent)",
                          }
                        : l.startsWith("-")
                          ? { background: "var(--err-bg)" }
                          : l.startsWith("@@")
                            ? { color: "var(--ink-faint)" }
                            : undefined
                    }
                  >
                    {l || " "}
                  </div>
                ))}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}
