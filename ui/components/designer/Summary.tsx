"use client";

import type {
  DesignerConnection,
  DesignerDraft,
  DesignerStep,
  DraftFile,
} from "@/lib/api";
import { DraftPanel, PanelTab, PanelTabs } from "./DraftFiles";
import { ConnectionRows } from "./Connect";
import { ErrorBox, Spinner } from "../bits";
import {
  BookIcon,
  ChartIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CloseIcon,
  FileIcon,
  LayersIcon,
  PlusIcon,
  ShieldIcon,
  SparkleIcon,
  UserIcon,
  UsersIcon,
  WandIcon,
} from "../icons";
import { KIND_NAME, reach, Section, TOOL_KIND } from "./parts";

const COVER_LABEL = {
  covered: "Covered",
  partly: "Partly",
  missing: "Not available",
} as const;
const COVER_STYLE = {
  covered: {
    background: "color-mix(in srgb, var(--ok) 14%, transparent)",
    color: "var(--ok)",
  },
  partly: { background: "var(--warn-bg)", color: "var(--warn-line)" },
  missing: { background: "var(--err-bg)", color: "var(--err)" },
} as const;

/** What the main button says: building straight away, or first reading the new tools. */
export function ctaLabel(draft: DesignerDraft): string {
  return draft.new_tools?.length ? "Review the new tools" : "Approve and build";
}

function missing(progress: DesignerStep[]): string[] {
  return progress.filter((p) => !p.done).map((p) => p.label);
}

const KIND_ICON: Record<string, JSX.Element> = {
  supervisor: <WandIcon size={20} />,
  genie: <ChartIcon size={20} />,
  knowledge: <BookIcon size={20} />,
};

function CoverMark({ status }: { status: "covered" | "partly" | "missing" }) {
  let mark: JSX.Element | string = <CloseIcon size={10} />;
  if (status === "covered") mark = <CheckIcon size={11} />;
  if (status === "partly") mark = "~";
  return (
    <span
      className="mt-0.5 inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full text-[12px] font-bold"
      style={COVER_STYLE[status]}
      title={COVER_LABEL[status]}
    >
      <span aria-hidden>{mark}</span>
      <span className="sr-only">{COVER_LABEL[status]}:</span>
    </span>
  );
}

function hint(ready: boolean, draft: DesignerDraft, todo: string[]): string {
  if (ready)
    return draft.new_tools?.length
      ? "Next you read the new tools. Nothing is created until you approve them."
      : "Nothing is created until you press this.";
  if (!todo.length) return "Answer the last question to unlock this.";
  return `Still needed: ${todo.slice(0, 2).join(", ")}${todo.length > 2 ? ` and ${todo.length - 2} more` : ""}`;
}

/** The assistant as it takes shape: who it is, how far along, the one button, then the
 *  details. The top stays put while the details scroll (on a wide screen), and anything
 *  that changes briefly lights up, so each answer visibly moves it forward. */
export function Summary({
  draft,
  progress,
  ready,
  building,
  busy,
  error,
  onApprove,
  connStatus = {},
  onConnect = () => {},
  collapsed = false,
  onToggle,
  writing = [],
  tab = "summary",
  onTab = () => {},
  files = null,
  filesErr = "",
  changed = 0,
}: {
  draft: DesignerDraft;
  progress: DesignerStep[];
  ready: boolean;
  building: boolean;
  busy: boolean;
  error?: string;
  onApprove: () => void;
  connStatus?: Record<string, boolean>;
  onConnect?: (c: DesignerConnection) => void;
  /** Folded into a slim rail beside the conversation (wide screens only). */
  collapsed?: boolean;
  onToggle?: () => void;
  writing?: { name: string; step: string; seconds: number }[];
  /** Summary, or the draft as a plan, its files and what the last reply changed (DraftFiles.tsx). */
  tab?: PanelTab;
  onTab?: (t: PanelTab) => void;
  files?: DraftFile[] | null;
  filesErr?: string;
  changed?: number;
}) {
  const total = progress.length;
  const done = progress.filter((p) => p.done).length;
  const empty = !draft.kind && !draft.display_name;
  const todo = missing(progress);
  const uses: { label: string; sub: string; icon: JSX.Element }[] = [
    ...(draft.tables || []).map((t) => ({
      label: t,
      sub: "Table",
      icon: <ChartIcon size={15} />,
    })),
    ...(draft.sources || []).map((s) => ({
      label: s.name || s.volume,
      sub: s.description || "Document folder",
      icon: <BookIcon size={15} />,
    })),
    ...(draft.tools || []).map((t) => ({
      label: t.description || t.ref,
      sub: TOOL_KIND[t.type] || "Tool",
      icon:
        t.type === "volume" ? <FileIcon size={15} /> : <WandIcon size={15} />,
    })),
  ];
  const cover = draft.coverage || [];
  const gotCover = cover.filter((c) => c.status === "covered").length;
  const row = (
    c: NonNullable<DesignerDraft["coverage"]>[number],
    i: number,
  ) => (
    <li key={i} className="flex min-w-0 gap-2.5 text-[14px]">
      <CoverMark status={c.status} />
      <span className="min-w-0">
        <span className="break-words">{c.need}</span>
        {c.note || c.by ? (
          <span className="mt-0.5 block break-words text-[12.5px] faint">
            {c.note || c.by}
          </span>
        ) : null}
      </span>
    </li>
  );

  const unconnected = (draft.connections || []).filter(
    (c) => !connStatus[c.name],
  );
  const live = ready && !busy;
  const button = (
    <button
      type="button"
      className={
        live
          ? "btn btn-primary btn-lg w-full"
          : "btn btn-primary btn-lg dz-cta-off w-full"
      }
      disabled={!ready || busy || building}
      onClick={onApprove}
    >
      {building ? <Spinner label="Working…" /> : ctaLabel(draft)}
    </button>
  );

  // Folded: a slim rail with how far along it is, and the button when it is ready.
  const rail = (
    <aside
      className="card hidden h-full flex-col items-center gap-3 px-2 py-3 lg:flex"
      aria-label="Your assistant so far (folded)"
    >
      <button
        type="button"
        className="dz-rail-btn"
        onClick={onToggle}
        aria-label="Show the preview"
        title="Show the preview"
      >
        <ChevronLeftIcon size={18} />
      </button>
      <span
        className="dz-ring-sm"
        title={`${done} of ${total} settled`}
        aria-label={`${done} of ${total} settled`}
      >
        <svg viewBox="0 0 44 44" aria-hidden>
          <circle
            cx="22"
            cy="22"
            r="19"
            fill="none"
            stroke="var(--bubble)"
            strokeWidth="4"
          />
          <circle
            cx="22"
            cy="22"
            r="19"
            fill="none"
            stroke="var(--brand)"
            strokeWidth="4"
            strokeLinecap="round"
            strokeDasharray={`${total ? (done / total) * 119.4 : 0} 119.4`}
          />
        </svg>
        <span className="text-[11.5px] font-bold tabular-nums">
          {done}/{total}
        </span>
      </span>
      <span
        className="dz-tile !h-9 !w-9"
        title={draft.display_name || "Untitled assistant"}
        aria-hidden
      >
        {(draft.kind && KIND_ICON[draft.kind]) || <SparkleIcon size={18} />}
      </span>
      {unconnected.length ? (
        <button
          type="button"
          className="dz-rail-btn"
          style={{ background: "var(--warn-bg)", color: "var(--warn-line)" }}
          onClick={() => onConnect(unconnected[0])}
          title={`Connect ${unconnected[0].label}`}
          aria-label={`Connect ${unconnected[0].label}`}
        >
          <ShieldIcon size={17} />
        </button>
      ) : null}
      <span className="flex-1" />
      {live ? (
        <button
          type="button"
          className="btn btn-primary !h-10 !w-10 !min-h-0 !rounded-xl !p-0"
          onClick={onApprove}
          disabled={building}
          title={ctaLabel(draft)}
          aria-label={ctaLabel(draft)}
        >
          <CheckIcon size={18} />
        </button>
      ) : null}
    </aside>
  );

  const full = (
    <aside
      id="designer-summary"
      className={`card relative flex h-fit flex-col overflow-hidden p-0 lg:h-full lg:min-h-0 ${collapsed ? "lg:hidden" : ""}`}
      aria-label="Your assistant so far"
    >
      <div className="shrink-0 px-5 pb-3 pt-4">
        <div className="flex items-center justify-between gap-3">
          <span className="dz-eyebrow flex items-center gap-1.5">
            <span
              aria-hidden
              className={
                busy
                  ? "h-1.5 w-1.5 animate-pulse rounded-full"
                  : "h-1.5 w-1.5 rounded-full"
              }
              style={{ background: "var(--brand)" }}
            />
            Live preview
          </span>
          <span className="flex items-center gap-2">
            <span
              className="text-[12.5px] tabular-nums muted"
              aria-live="polite"
            >
              {done} of {total} settled
            </span>
            {onToggle ? (
              <button
                type="button"
                className="icon-btn !hidden !h-8 !w-8 lg:!inline-flex"
                onClick={onToggle}
                aria-label="Hide the preview"
                title="Hide the preview"
              >
                <ChevronRightIcon size={16} />
              </button>
            ) : null}
          </span>
        </div>

        <div className="mt-3 flex items-center gap-3">
          <span className="dz-tile !h-10 !w-10 !rounded-[13px]" aria-hidden>
            {(draft.kind && KIND_ICON[draft.kind]) || <SparkleIcon size={20} />}
          </span>
          <div
            key={(draft.display_name || "") + (draft.kind || "")}
            className={empty ? "min-w-0 flex-1" : "dz-flash min-w-0 flex-1"}
          >
            {draft.display_name ? (
              <p
                className="truncate text-[15.5px] font-semibold"
                title={draft.display_name}
              >
                {draft.display_name}
              </p>
            ) : (
              <p className="text-[15.5px] font-semibold faint">
                Untitled assistant
              </p>
            )}
            <p className="truncate text-[12.5px] muted">
              {draft.kind
                ? KIND_NAME[draft.kind]
                : "Its kind is worked out as you answer"}
            </p>
          </div>
        </div>

        <div
          className="dz-seg mt-3"
          role="progressbar"
          aria-label="How much is settled"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={done}
        >
          {progress.map((p) => (
            <span
              key={p.key}
              data-on={p.done ? "true" : "false"}
              title={p.label}
            />
          ))}
        </div>
        <PanelTabs
          tab={tab}
          onTab={onTab}
          changed={changed}
          open={(draft.open_questions || []).length}
        />
      </div>

      {/* relative: hidden screen-reader labels (position: absolute) stay inside this scrolling area instead of
          stretching the page (seen live: a grey band under the app on a short window). */}
      <div
        className="relative border-t px-5 pb-5 pt-4 lg:min-h-0 lg:flex-1 lg:overflow-y-auto"
        style={{ borderColor: "var(--line)" }}
      >
        {tab !== "summary" ? (
          <DraftPanel tab={tab} draft={draft} files={files} err={filesErr} />
        ) : (
          <>
            <ul className="mb-5 grid gap-1.5">
              {progress.map((p) => (
                <li
                  key={p.key}
                  className="flex items-center gap-2 text-[13.5px]"
                >
                  {p.done ? (
                    <span
                      aria-hidden
                      className="inline-flex h-4 w-4 items-center justify-center rounded-full text-white"
                      style={{
                        background:
                          "linear-gradient(135deg, var(--mark-from), var(--mark-to))",
                      }}
                    >
                      <CheckIcon size={10} />
                    </span>
                  ) : (
                    <span
                      aria-hidden
                      className="inline-block h-4 w-4 rounded-full"
                      style={{ border: "1.5px dashed var(--line)" }}
                    />
                  )}
                  <span className={p.done ? "" : "muted"}>{p.label}</span>
                  <span className="sr-only">
                    {p.done ? "(settled)" : "(still needed)"}
                  </span>
                </li>
              ))}
            </ul>
            {empty ? (
              <div>
                <p className="text-[13.5px] muted">
                  As you answer, what it does, what it uses and who can use it
                  appear here.
                </p>
                <div className="mt-5 space-y-3" aria-hidden>
                  <div className="dz-placeholder w-1/3" />
                  <div className="dz-placeholder w-full" />
                  <div className="dz-placeholder w-5/6" />
                  <div className="dz-placeholder mt-6 w-1/4" />
                  <div className="dz-placeholder w-3/4" />
                  <div className="dz-placeholder w-2/3" />
                </div>
              </div>
            ) : (
              <div>
                {draft.description ? (
                  <Section
                    title="What it does"
                    icon={<SparkleIcon size={13} />}
                    flash={draft.description}
                  >
                    <p className="leading-relaxed">{draft.description}</p>
                  </Section>
                ) : null}
                {uses.length ? (
                  <Section
                    title="What it uses"
                    icon={<LayersIcon size={13} />}
                    flash={uses.map((u) => u.label).join("|")}
                  >
                    <ul className="space-y-2.5">
                      {uses.slice(0, 8).map((u, i) => (
                        <li key={i} className="flex min-w-0 gap-2.5">
                          <span
                            className="dz-tile !h-7 !w-7 !rounded-lg"
                            aria-hidden
                          >
                            {u.icon}
                          </span>
                          <span className="min-w-0">
                            <span className="block break-words text-[14px] leading-snug">
                              {u.label}
                            </span>
                            <span className="block text-[12px] faint">
                              {u.sub}
                            </span>
                          </span>
                        </li>
                      ))}
                      {uses.length > 8 ? (
                        <li className="text-sm muted">
                          and {uses.length - 8} more
                        </li>
                      ) : null}
                    </ul>
                  </Section>
                ) : null}
                {writing.length ? (
                  <Section
                    title="New tools being written"
                    icon={<PlusIcon size={13} />}
                  >
                    <ul className="space-y-2">
                      {writing.map((w) => (
                        <li
                          key={w.name}
                          className="flex min-w-0 items-center gap-2.5 rounded-xl px-3 py-2"
                          style={{ background: "var(--bubble)" }}
                        >
                          <span
                            className="dz-spin shrink-0"
                            style={{ color: "var(--brand-deep)" }}
                            aria-hidden
                          />
                          <span className="min-w-0">
                            <span className="block truncate text-[14px] font-medium">
                              {w.name}
                            </span>
                            <span className="block truncate text-[12.5px] faint">
                              {w.step}
                            </span>
                          </span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-2 text-[12.5px] faint">
                      Usually 2 to 5 minutes. You can keep answering meanwhile.
                    </p>
                  </Section>
                ) : null}
                {draft.new_tools?.length ? (
                  <Section
                    title="New tools to create"
                    icon={<PlusIcon size={13} />}
                    flash={draft.new_tools.map((n) => n.fingerprint).join("|")}
                  >
                    <ul className="space-y-2">
                      {draft.new_tools.map((n) => (
                        <li
                          key={n.fingerprint}
                          className="min-w-0 rounded-xl px-3 py-2"
                          style={{ background: "var(--bubble)" }}
                        >
                          <span className="break-words text-[14px] font-medium">
                            {n.name}
                          </span>
                          <span className="block break-words text-[12.5px] faint">
                            {reach(n).join(" · ")}
                          </span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-2 text-[12.5px] faint">
                      You read each one before anything is created.
                    </p>
                  </Section>
                ) : null}
                {draft.connections?.length ? (
                  <Section
                    title="Connections"
                    icon={<ShieldIcon size={13} />}
                    flash={draft.connections
                      .map((c) => c.name + (connStatus[c.name] ? "1" : "0"))
                      .join("|")}
                  >
                    <ConnectionRows
                      list={draft.connections}
                      status={connStatus}
                      onConnect={onConnect}
                    />
                    <p className="mt-2 text-[12.5px] faint">
                      Saved in your workspace&apos;s secret store. The AI model
                      never sees them.
                    </p>
                  </Section>
                ) : null}
                {draft.access_decided ? (
                  <Section
                    title="Who can use it"
                    icon={<UsersIcon size={13} />}
                    flash={
                      (draft.access || []).map((a) => a.principal).join("|") ||
                      "nobody"
                    }
                  >
                    {draft.access?.length ? (
                      <div className="flex flex-wrap gap-1.5">
                        {draft.access.map((a) => (
                          <span
                            key={a.kind + a.principal}
                            className="dz-pill"
                            title={a.kind === "group" ? "Team" : "Person"}
                          >
                            {a.kind === "group" ? (
                              <UsersIcon size={12} />
                            ) : (
                              <UserIcon size={12} />
                            )}
                            <span className="truncate">{a.principal}</span>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="muted">
                        Nobody yet. You can share it later.
                      </p>
                    )}
                  </Section>
                ) : null}
                {cover.length ? (
                  <Section
                    title={`Checked against your request · ${gotCover} of ${cover.length}`}
                    icon={<ShieldIcon size={13} />}
                    flash={cover.map((c) => c.need + c.status).join("|")}
                  >
                    <ul className="space-y-2.5">
                      {cover.slice(0, 4).map(row)}
                    </ul>
                    {cover.length > 4 ? (
                      <details className="mt-2.5">
                        <summary
                          className="cursor-pointer text-[13px] font-medium"
                          style={{ color: "var(--brand-deep)" }}
                        >
                          Show the other {cover.length - 4}
                        </summary>
                        <ul className="mt-2.5 space-y-2.5">
                          {cover.slice(4).map((c, i) => row(c, i + 4))}
                        </ul>
                      </details>
                    ) : null}
                  </Section>
                ) : null}
                {draft.gaps?.length ? (
                  <Section title="Not included" icon={<CloseIcon size={12} />}>
                    <ul className="space-y-1.5 text-[14px]">
                      {draft.gaps.map((g) => (
                        <li key={g} className="flex gap-2">
                          <span aria-hidden className="faint">
                            –
                          </span>
                          <span className="min-w-0 break-words muted">{g}</span>
                        </li>
                      ))}
                    </ul>
                  </Section>
                ) : null}
                {draft.instructions || draft.notes ? (
                  <details
                    className="mt-5 rounded-xl px-3.5 py-2.5 text-sm"
                    style={{ background: "var(--bubble)" }}
                  >
                    <summary className="cursor-pointer font-medium">
                      How it will behave
                    </summary>
                    <p className="mt-2 whitespace-pre-wrap break-words leading-relaxed muted">
                      {draft.instructions || draft.notes}
                    </p>
                  </details>
                ) : null}
              </div>
            )}
          </>
        )}
      </div>

      {/* The one button, always in view at the bottom, however long the details grow. */}
      <div
        className="shrink-0 border-t px-5 pb-4 pt-3"
        style={{ borderColor: "var(--line)", background: "var(--surface)" }}
      >
        {error ? (
          <div className="mb-3">
            <ErrorBox>{error}</ErrorBox>
          </div>
        ) : null}
        {button}
        <p className="mt-1.5 text-center text-[12px] faint">
          {hint(ready, draft, todo)}
        </p>
        {ready && tab !== "plan" ? (
          <button
            type="button"
            className="mt-1 block w-full text-center text-[12.5px] font-medium underline"
            style={{ color: "var(--brand-deep)" }}
            onClick={() => onTab("plan")}
          >
            Read the plan before you approve
          </button>
        ) : null}
      </div>
    </aside>
  );

  return (
    <>
      {collapsed ? rail : null}
      {full}
    </>
  );
}

/** On a phone the summary sits below a long conversation. This slim strip above the chat
 *  says how far along it is and jumps to it. It is a button that scrolls, not a link:
 *  this app uses the URL hash for its own navigation. It is not fixed to the screen,
 *  because anything fixed there covers the box people type in. */
export function ProgressStrip({ progress }: { progress: DesignerStep[] }) {
  const done = progress.filter((p) => p.done).length;
  const total = progress.length;
  return (
    <button
      type="button"
      className="card mb-3 flex w-full items-center gap-3 px-4 py-3 text-left lg:hidden"
      onClick={() =>
        document
          .getElementById("designer-summary")
          ?.scrollIntoView({ behavior: "smooth", block: "start" })
      }
    >
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-3 text-sm">
          <span className="font-medium">Your assistant</span>
          <span className="muted">
            {done} of {total} settled
          </span>
        </span>
        <span className="dz-seg mt-1.5" aria-hidden>
          {progress.map((p) => (
            <span key={p.key} data-on={p.done ? "true" : "false"} />
          ))}
        </span>
      </span>
      <span className="shrink-0 text-sm" style={{ color: "var(--brand-deep)" }}>
        See details ↓
      </span>
    </button>
  );
}
