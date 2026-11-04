"use client";

import { ReactNode, useEffect, useMemo, useState } from "react";
import { ActivityFeed, AgentHealthRow, api, AuditEvent, HealthReport, LogsResult, PortalEvent } from "@/lib/api";
import { initials, middleShort, nameOf } from "@/lib/people";
import { ErrorBox, Pager, usePage } from "./bits";
import { SearchIcon } from "./icons";

/** Audit log and Monitoring, the two operational views, in one visual
 *  language: a toolbar (view on the left, period on the right), one card per
 *  list with its filters in the header and a pager in the footer, and the same
 *  row everywhere - a mark, a sentence, the time, a tag only when something went
 *  wrong, and the detail on click. Usage and spend live in Insights, so they are
 *  not repeated here; nothing anyone typed is ever shown. */

// ---------------------------------------------------------------- shared ----

function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    let live = true;
    setBusy(true);
    setErr("");
    load()
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(e.message))
      .finally(() => live && setBusy(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, err, busy };
}

function Seg<T extends string | number>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={String(v)} type="button" aria-pressed={value === v} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

/** The same three periods as Insights and My dashboard. */
function Toolbar({ left, days, setDays }: { left: ReactNode; days: number; setDays: (d: number) => void }) {
  return (
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">{left}</div>
      <Seg
        label="Time period"
        value={days}
        onChange={setDays}
        options={[
          [7, "7 days"],
          [30, "30 days"],
          [90, "90 days"],
        ]}
      />
    </div>
  );
}

export function Card({ title, sub, filters, children, pager }: { title?: string; sub?: string; filters?: ReactNode; children: ReactNode; pager?: ReactNode }) {
  return (
    <section className="card min-w-0 overflow-hidden">
      {title || filters ? (
        <div className="border-b px-5 py-3.5" style={{ borderColor: "var(--line)" }}>
          {title ? (
            <div className={filters ? "mb-3" : ""}>
              <h2 className="text-[15px] font-semibold tracking-[-0.01em]">{title}</h2>
              {sub ? <p className="mt-0.5 text-[13px] faint">{sub}</p> : null}
            </div>
          ) : null}
          {filters ? <div className="flex flex-wrap items-center gap-3">{filters}</div> : null}
        </div>
      ) : null}
      {children}
      {pager}
    </section>
  );
}

export function Chips({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: [string, string, number?][]; label: string }) {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label={label}>
      {options.map(([k, text, n]) => (
        <button key={k || "all"} type="button" className="filter-chip !min-h-[32px]" aria-pressed={value === k} onClick={() => onChange(k)}>
          {text}
          {typeof n === "number" ? <span className="count">{n.toLocaleString()}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Find({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <label className="field flex w-full items-center gap-2 !py-0 sm:ml-auto sm:w-60">
      <span className="faint">
        <SearchIcon size={16} />
      </span>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="min-h-[34px] w-full bg-transparent outline-none"
      />
    </label>
  );
}

export function Quiet({ children }: { children: ReactNode }) {
  return <p className="px-5 py-10 text-center text-sm muted">{children}</p>;
}

export function Tag({ tone, children }: { tone: "ok" | "warn" | "bad" | "muted"; children: ReactNode }) {
  const style =
    tone === "ok"
      ? { background: "color-mix(in srgb, var(--ok) 12%, transparent)", color: "var(--ok)" }
      : tone === "warn"
        ? { background: "var(--warn-bg)", color: "var(--warn-line)" }
        : tone === "bad"
          ? { background: "var(--err-bg)", color: "var(--err)" }
          : { background: "var(--bubble)", color: "var(--ink-faint)" };
  return (
    <span className="status-pill shrink-0 whitespace-nowrap" style={style}>
      <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: "currentColor" }} />
      {children}
    </span>
  );
}

/** The one row every list uses. `detail` makes it open on click. */
export function Row({
  mark,
  title,
  meta,
  tag,
  detail,
  open,
  onToggle,
}: {
  mark: ReactNode;
  title: ReactNode;
  meta: ReactNode;
  tag?: ReactNode;
  detail?: ReactNode;
  open?: boolean;
  onToggle?: () => void;
}) {
  const body = (
    <>
      <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold" style={{ background: "var(--brand-soft)", color: "var(--brand-deep)" }} aria-hidden>
        {mark}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm leading-snug">{title}</span>
        <span className="mt-0.5 block text-xs faint">{meta}</span>
        {detail && open ? <span className="mt-2 block">{detail}</span> : null}
      </span>
      {tag}
    </>
  );
  const cls = "flex w-full items-start gap-3 px-5 py-3 text-left";
  return (
    <li style={{ borderTop: "1px solid var(--line)" }}>
      {detail ? (
        <button type="button" className={`${cls} transition hover:bg-[var(--canvas)]`} onClick={onToggle} aria-expanded={!!open}>
          {body}
        </button>
      ) : (
        <div className={cls}>{body}</div>
      )}
    </li>
  );
}

export function Pre({ children, tone = "err" }: { children: ReactNode; tone?: "err" | "plain" }) {
  return (
    <pre
      className="whitespace-pre-wrap break-words rounded-lg px-3 py-2 text-[12px] leading-relaxed"
      style={tone === "err" ? { background: "var(--err-bg)", color: "var(--err)" } : { background: "var(--canvas)", border: "1px solid var(--line)" }}
    >
      {children}
    </pre>
  );
}

/** Rows grouped under Today / Yesterday / a date. */
function ByDay<T extends { at: string }>({ items, render }: { items: T[]; render: (item: T, key: string) => ReactNode }) {
  const groups = useMemo(() => {
    const g: { day: string; rows: { item: T; key: string }[] }[] = [];
    items.forEach((item, i) => {
      const d = dayLabel(item.at);
      if (!g.length || g[g.length - 1].day !== d) g.push({ day: d, rows: [] });
      g[g.length - 1].rows.push({ item, key: `${item.at}-${i}` });
    });
    return g;
  }, [items]);
  return (
    <div>
      {groups.map((g) => (
        <div key={g.day}>
          <p className="px-5 pb-1.5 pt-4 text-[11px] font-semibold uppercase tracking-[0.1em] faint">{g.day}</p>
          <ul>{g.rows.map(({ item, key }) => render(item, key))}</ul>
        </div>
      ))}
    </div>
  );
}

function NotKept({ stored }: { stored?: boolean }) {
  if (stored !== false) return null;
  return <p className="mt-4 text-xs faint">Kept in memory for this running copy only, so a restart clears it. Set PORTAL_LOG_TABLE to keep it.</p>;
}

function Dim({ busy, children }: { busy: boolean; children: ReactNode }) {
  return <div style={{ opacity: busy ? 0.55 : 1, transition: "opacity .15s" }}>{children}</div>;
}

function who(actor: string) {
  if (!actor || actor === "unknown") return "Someone";
  return nameOf(actor) || actor;
}

function clock(iso: string) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function dayLabel(iso: string) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "Earlier";
  const today = new Date();
  const y = new Date(today);
  y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

function ago(iso: string) {
  const t = Date.parse(iso);
  if (!t) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** Response time, always one decimal under a minute: "2.8 s", "28.0 s", "1.4 min". */
function secs(ms: number) {
  if (!ms) return "—";
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return `${(ms / 60000).toFixed(1)} min`;
}

/** Whole percentages everywhere, so a column reads evenly; tiny rates say "<1%". */
function pct(r: number) {
  if (r > 0 && r < 0.01) return "<1%";
  return `${Math.round(r * 100)}%`;
}

const PAGE = 25;

// ------------------------------------------------------------- audit log ----

export function AuditLogPage() {
  const [tab, setTab] = useState<"portal" | "databricks">("portal");
  const [days, setDays] = useState(7);
  return (
    <div>
      <Toolbar
        days={days}
        setDays={setDays}
        left={
          <Seg
            label="Source"
            value={tab}
            onChange={setTab}
            options={[
              ["portal", "Portal"],
              ["databricks", "Databricks"],
            ]}
          />
        }
      />
      {tab === "portal" ? <PortalFeed days={days} /> : <DatabricksFeed days={days} />}
    </div>
  );
}

const FIELD: Record<string, string> = {
  display_name: "name",
  blurb: "description",
  upload_volume: "upload folder",
  output_volume: "results folder",
  accepts: "accepted file types",
  portal: "visibility",
};

/** One portal event as a sentence anyone can read. */
function sentence(e: PortalEvent): ReactNode {
  const d = e.detail || {};
  const b = (t: string) => <strong className="font-semibold">{t}</strong>;
  const label = middleShort(e.label || e.target || "an assistant", 48);
  const file = middleShort(d.name || "a file", 40);
  const name = b(who(e.actor));
  switch (e.action) {
    case "opened_portal":
      return <>{name} signed in</>;
    case "asked":
      return <>{name} asked {b(label)} a question{d.files ? " with a file" : ""}</>;
    case "deleted_conversation":
      return <>{name} deleted a conversation</>;
    case "uploaded":
      return <>{name} uploaded {b(file)} to {b(label)}</>;
    case "downloaded":
      return <>{name} downloaded {b(file)} from {b(label)}</>;
    case "granted":
      return <>{name} granted {b(d.principal || "someone")} access to {b(label)}</>;
    case "revoked":
      return <>{name} revoked {b(d.principal || "someone")}’s access to {b(label)}</>;
    case "created_team":
      return <>{name} created the team {b(label)}</>;
    case "changed_team":
      return <>{name} updated team membership</>;
    case "changed_settings":
      return <>{name} updated the {(d.fields || []).map((f: string) => FIELD[f] || f.replace(/_/g, " ")).join(", ") || "settings"} of {b(label)}</>;
    case "created_assistant":
      return <>{name} created {b(label)}{(d.deploying || []).length ? <>, setting up {(d.deploying as string[]).join(", ")}</> : null}</>;
    case "fixed_tool":
      return <>{name} repaired the tool {b(label)}{d.what ? <>: {String(d.what)}</> : null}</>;
    case "created_tool":
      return <>{name} created the new {(d.tools || []).length === 1 ? "tool" : "tools"} {b(label)}</>;
    case "edited_assistant":
      return <>{name} updated {b(label)}{(d.deploying || []).length ? <>, setting up {(d.deploying as string[]).join(", ")}</> : null}</>;
    case "deleted_assistant":
      return <>{name} deleted an assistant</>;
    case "attached_file":
      return <>{name} attached {b(label)} while designing an assistant</>;
    case "tested_assistant":
      return <>{name} tested {b(label)}: {d.good ?? "?"} of {d.total ?? "?"} answers good</>;
    case "sent_for_approval":
      return <>{name} sent {b(label)} for approval{(d.approvers || []).length ? <> to {(d.approvers as string[]).join(", ")}</> : null}</>;
    case "approved_release":
      return <>{name} approved {b(label)}; it is now live</>;
    case "sent_back":
      return <>{name} sent {b(label)} back for changes</>;
    case "restored_version":
      return <>{name} put version {d.version ?? "?"} of {b(label)} back live</>;
    case "retired_assistant":
      return <>{name} retired {b(label)}</>;
    case "saved_connection":
      return <>{name} {d.replaced ? "replaced" : "saved"} the connection {b(label)}</>;
    default:
      return <>{name} {e.action.replace(/_/g, " ")} {label}</>;
  }
}

function PortalFeed({ days }: { days: number }) {
  const feed = useLoad<ActivityFeed>(() => api.activityFeed(days), [days]);
  const [cat, setCat] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const f = feed.data;
  const all = f?.events || [];
  const c = f?.categories || {};
  const s = q.trim().toLowerCase();
  const list = all.filter(
    (e) =>
      (!cat || (cat === "errors" ? e.status !== "ok" : e.category === cat)) &&
      (!s || e.actor.toLowerCase().includes(s) || who(e.actor).toLowerCase().includes(s) || (e.label || "").toLowerCase().includes(s))
  );
  const pg = usePage(list, PAGE, [days, cat, q]);

  if (feed.err && !f) return <ErrorBox>{feed.err}</ErrorBox>;
  return (
    <Dim busy={feed.busy && !!f}>
      <Card
        filters={
          <>
            <Chips
              label="Event type"
              value={cat}
              onChange={setCat}
              options={[
                ["", "All events", f?.total ?? 0],
                ["questions", "Questions", c.questions || 0],
                ["files", "Files", c.files || 0],
                ["access", "Access", c.access || 0],
                ["build", "Builder", c.build || 0],
                ["sessions", "Sign-ins", c.sessions || 0],
                ["errors", "Errors", f?.failed ?? 0],
              ]}
            />
            <Find value={q} onChange={setQ} placeholder="Search user or assistant" />
          </>
        }
        pager={<Pager pg={pg} noun="events" />}
      >
        {!f ? (
          <Quiet>Loading…</Quiet>
        ) : !list.length ? (
          <Quiet>{all.length ? "No events match these filters." : "No events in this period."}</Quiet>
        ) : (
          <ByDay
            items={pg.rows}
            render={(e, key) => (
              <Row
                key={key}
                mark={initials(e.actor === "unknown" ? "?" : e.actor)}
                title={sentence(e)}
                meta={<span title={e.actor}>{clock(e.at)}</span>}
                tag={e.status === "ok" ? null : <Tag tone="bad">{e.status === "tool_error" ? "Tool error" : "Error"}</Tag>}
                detail={e.status !== "ok" && e.error ? <Pre>{e.error}</Pre> : undefined}
                open={open === key}
                onToggle={() => setOpen(open === key ? null : key)}
              />
            )}
          />
        )}
      </Card>
      <NotKept stored={f?.stored} />
    </Dim>
  );
}

/** Databricks' own record of access changes, including ones made outside the
 *  portal - the one thing this tab is for. Deployments, sign-ins and internal
 *  checks are deliberately left out: they answer no admin question here. */
function mark(e: AuditEvent) {
  if (!e.ok) return "!";
  return ({ grant: "+", revoke: "−", deploy: "↻", data: "▤", group: "◍" } as Record<string, string>)[e.kind] || "•";
}

function DatabricksFeed({ days }: { days: number }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const feed = useLoad(() => api.audit(days, "access"), [days]);

  const d = feed.data;
  const s = q.trim().toLowerCase();
  const list = (d?.events || []).filter((e) => !s || e.message.toLowerCase().includes(s) || (e.actor || "").toLowerCase().includes(s));
  const pg = usePage(list, PAGE, [days, q]);

  if (feed.err && !d) return <ErrorBox>{feed.err}</ErrorBox>;
  return (
    <Dim busy={feed.busy && !!d}>
      <Card
        filters={
          <>
            <p className="min-w-0 flex-1 text-[13px] muted">
              Access granted or removed in Databricks itself, outside the portal. New changes can take a few minutes to appear.
            </p>
            <Find value={q} onChange={setQ} placeholder="Search changes" />
          </>
        }
        pager={<Pager pg={pg} noun="changes" />}
      >
        {!d ? (
          <Quiet>Loading…</Quiet>
        ) : !d.available ? (
          <Quiet>The Databricks audit log cannot be read right now. {d.note}</Quiet>
        ) : !list.length ? (
          <Quiet>{d.events.length ? "No changes match this search." : "No access changes in Databricks in this period."}</Quiet>
        ) : (
          <ByDay
            items={pg.rows}
            render={(e, key) => (
              <Row
                key={key}
                mark={mark(e)}
                title={e.message}
                meta={clock(e.at)}
                tag={e.ok ? null : <Tag tone="bad">Error</Tag>}
                detail={
                  <Pre tone={e.ok ? "plain" : "err"}>
                    {!e.ok && e.error ? e.error + "\n\n" : ""}
                    {e.detail.service}.{e.detail.action}
                    {e.detail.ip ? `  from ${e.detail.ip}` : ""}
                    {"\n"}
                    {JSON.stringify(e.detail.params, null, 1)}
                  </Pre>
                }
                open={open === key}
                onToggle={() => setOpen(open === key ? null : key)}
              />
            )}
          />
        )}
      </Card>
    </Dim>
  );
}

// ------------------------------------------------------------ monitoring ----

const FIX: Record<string, string> = {
  no_access: "The user is not allowed to use this assistant. Grant them, or their team, access on the Access page.",
  data_access: "The assistant reached data the user cannot read. Grant them access to the tables or volume in Unity Catalog.",
  timeout: "The assistant did not respond in time or could not be reached. If it persists, check the serving endpoint.",
  not_ready: "The serving endpoint was starting or scaled to zero. It normally recovers within a few minutes.",
  tool_error: "A tool the assistant calls, such as an app or function, returned an error. Check that tool.",
  rate_limit: "The serving endpoint rejected requests over its rate limit. Raise the limit or retry later.",
  bad_request: "The assistant rejected the request, which usually points to a configuration problem.",
  agent_error: "The assistant failed while answering. Its serving endpoint logs in Databricks give the cause.",
  other: "Open an error to read the full message.",
};

type Grade = "failing" | "degraded" | "healthy" | "low";
const GRADES: Record<Grade, { label: string; tone: "bad" | "warn" | "ok" | "muted" }> = {
  failing: { label: "Failing", tone: "bad" },
  degraded: { label: "Degraded", tone: "warn" },
  healthy: { label: "Healthy", tone: "ok" },
  low: { label: "Low traffic", tone: "muted" },
};
const LOW_TRAFFIC = 3;

function grade(a: AgentHealthRow): Grade {
  if (a.questions < LOW_TRAFFIC) return "low";
  if (a.failure_rate >= 0.25) return "failing";
  if (a.failure_rate >= 0.05) return "degraded";
  return "healthy";
}

export function MonitoringPage() {
  const [days, setDays] = useState(7);
  const rep = useLoad<HealthReport>(() => api.agentHealth(days), [days]);
  const h = rep.data;
  const counts = { failing: 0, degraded: 0, healthy: 0, low: 0 } as Record<Grade, number>;
  for (const a of h?.agents || []) counts[grade(a)]++;
  const attention = counts.failing + counts.degraded;
  const rate = h && h.questions ? h.failed / h.questions : 0;

  const summary = !h ? (
    <span className="text-sm muted">Loading…</span>
  ) : !h.questions ? (
    <span className="text-[15px] font-semibold">No questions in the last {days} days</span>
  ) : (
    <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
      <span aria-hidden className="h-2.5 w-2.5 rounded-full" style={{ background: counts.failing ? "var(--err)" : attention ? "var(--warn-line)" : "var(--ok)" }} />
      <span className="text-[15px] font-semibold">
        {attention ? `${attention} assistant${attention === 1 ? "" : "s"} need${attention === 1 ? "s" : ""} attention` : "All systems operational"}
      </span>
      <span className="text-sm muted">
        · {pct(rate)} error rate across {h.questions.toLocaleString()} {h.questions === 1 ? "question" : "questions"} · {secs(h.avg_ms)} average response
      </span>
    </span>
  );

  if (rep.err && !h) return <ErrorBox>{rep.err}</ErrorBox>;
  return (
    <Dim busy={rep.busy && !!h}>
      <Toolbar days={days} setDays={setDays} left={summary} />
      <div className="space-y-6">
        <Reliability report={h} counts={counts} days={days} />
        {h && h.kinds.length ? <ErrorsByCause report={h} days={days} /> : null}
        <SystemLogToggle days={days} />
      </div>
      <NotKept stored={h?.stored} />
    </Dim>
  );
}

function Reliability({ report: h, counts, days }: { report: HealthReport | null; counts: Record<Grade, number>; days: number }) {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const s = q.trim().toLowerCase();
  const rows = (h?.agents || []).filter((a) => (!status || grade(a) === status) && (!s || a.label.toLowerCase().includes(s)));
  const pg = usePage(rows, PAGE, [days, status, q]);
  const label = (kind: string) => h?.kinds.find((k) => k.kind === kind)?.label || "Error";

  return (
    <Card
      title="Assistant reliability"
      sub="Sorted by error rate. Assistants with fewer than 3 questions show as low traffic."
      filters={
        h && h.agents.length ? (
          <>
            <Chips
              label="Status"
              value={status}
              onChange={setStatus}
              options={[
                ["", "All", h.agents.length],
                ["failing", "Failing", counts.failing],
                ["degraded", "Degraded", counts.degraded],
                ["healthy", "Healthy", counts.healthy],
                ["low", "Low traffic", counts.low],
              ]}
            />
            <Find value={q} onChange={setQ} placeholder="Search assistants" />
          </>
        ) : null
      }
      pager={<Pager pg={pg} noun="assistants" />}
    >
      {!h ? (
        <Quiet>Loading…</Quiet>
      ) : !h.agents.length ? (
        <Quiet>No assistant was asked a question in this period.</Quiet>
      ) : !rows.length ? (
        <Quiet>No assistants match these filters.</Quiet>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs faint" style={{ background: "var(--canvas)" }}>
                <th className="whitespace-nowrap py-2.5 pl-5 pr-3 font-medium">Assistant</th>
                <th className="whitespace-nowrap px-3 py-2.5 font-medium">Status</th>
                <th className="whitespace-nowrap px-3 py-2.5 text-right font-medium">Questions</th>
                <th className="whitespace-nowrap px-3 py-2.5 text-right font-medium">Errors</th>
                <th className="whitespace-nowrap px-3 py-2.5 text-right font-medium">Error rate</th>
                <th className="whitespace-nowrap px-3 py-2.5 text-right font-medium">Avg. response</th>
                <th className="whitespace-nowrap px-3 py-2.5 pr-5 font-medium">Last error</th>
              </tr>
            </thead>
            <tbody>
              {pg.rows.map((a) => {
                const g = GRADES[grade(a)];
                return (
                  <tr key={a.endpoint} style={{ borderTop: "1px solid var(--line)" }}>
                    <td className="max-w-[300px] py-3 pl-5 pr-3">
                      <span className="block truncate font-medium" title={a.label}>
                        {middleShort(a.label, 40)}
                      </span>
                    </td>
                    <td className="px-3 py-3">
                      <Tag tone={g.tone}>{g.label}</Tag>
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{a.questions.toLocaleString()}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{a.failed.toLocaleString()}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{pct(a.failure_rate)}</td>
                    <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums">{secs(a.avg_ms)}</td>
                    <td className="whitespace-nowrap px-3 py-3 pr-5">
                      {a.last_problem ? (
                        <>
                          {label(a.last_problem.kind)}
                          <span className="faint"> · {ago(a.last_problem.at)}</span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function ErrorsByCause({ report: h, days }: { report: HealthReport; days: number }) {
  const [kind, setKind] = useState<string | null>(null);
  useEffect(() => setKind(null), [days]);
  return (
    <Card title="Errors by cause" sub="What went wrong and how to fix it. Open a cause to see each error.">
      <ul>
        {h.kinds.map((k) => (
          <Cause key={k.kind} kind={k.kind} label={k.label} count={k.count} items={h.recent.filter((e) => (e.error_kind || "other") === k.kind)} open={kind === k.kind} onToggle={() => setKind(kind === k.kind ? null : k.kind)} />
        ))}
      </ul>
    </Card>
  );
}

function Cause({ kind, label, count, items, open, onToggle }: { kind: string; label: string; count: number; items: PortalEvent[]; open: boolean; onToggle: () => void }) {
  const [row, setRow] = useState<string | null>(null);
  const pg = usePage(items, 10, [open]);
  return (
    <li style={{ borderTop: "1px solid var(--line)" }} className="first:border-t-0">
      <button type="button" className="flex w-full items-start gap-4 px-5 py-3.5 text-left transition hover:bg-[var(--canvas)]" onClick={onToggle} aria-expanded={open}>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold">{label}</span>
          <span className="mt-0.5 block text-[13px] muted">{FIX[kind] || FIX.other}</span>
        </span>
        <span className="shrink-0 text-right">
          <span className="block text-sm font-semibold tabular-nums">{count.toLocaleString()}</span>
          <span className="block text-xs" style={{ color: "var(--brand-deep)" }}>
            {open ? "Hide" : "View"}
          </span>
        </span>
      </button>
      {open ? (
        <div style={{ background: "var(--canvas)" }}>
          <ul>
            {pg.rows.map((e, i) => {
              const key = `${e.at}-${i}`;
              return (
                <Row
                  key={key}
                  mark={initials(e.actor === "unknown" ? "?" : e.actor)}
                  title={
                    <>
                      <strong className="font-semibold">{middleShort(e.label || e.target, 40)}</strong>
                      <span className="muted"> · {who(e.actor)}</span>
                    </>
                  }
                  meta={`${dayLabel(e.at)} at ${clock(e.at)}`}
                  detail={<Pre>{e.error || "No message was returned."}</Pre>}
                  open={row === key}
                  onToggle={() => setRow(row === key ? null : key)}
                />
              );
            })}
          </ul>
          <Pager pg={pg} noun="errors" />
        </div>
      ) : null}
    </li>
  );
}

function SystemLogToggle({ days }: { days: number }) {
  const [show, setShow] = useState(false);
  return (
    <div>
      <button type="button" className="text-[13px] font-medium" style={{ color: "var(--brand-deep)" }} onClick={() => setShow(!show)} aria-expanded={show}>
        {show ? "Hide system log" : "View system log"}
      </button>
      <span className="ml-2 text-xs faint">Portal warnings and failed calls to Databricks, for whoever maintains the portal.</span>
      {show ? (
        <div className="mt-3">
          <SystemLog days={days} />
        </div>
      ) : null}
    </div>
  );
}

function SystemLog({ days }: { days: number }) {
  const [tick, setTick] = useState(0);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const log = useLoad<LogsResult>(() => api.logs(days), [days, tick]);
  const s = q.trim().toLowerCase();
  const lines = (log.data?.lines || []).filter((l) => !s || l.message.toLowerCase().includes(s));
  const pg = usePage(lines, PAGE, [days, q, tick]);
  return (
    <Card
      title="System log"
      sub={log.data?.note || undefined}
      filters={
        <>
          <Find value={q} onChange={setQ} placeholder="Search messages" />
          <button type="button" className="btn btn-quiet !min-h-[34px] !text-[13px]" onClick={() => setTick(tick + 1)}>
            Refresh
          </button>
        </>
      }
      pager={<Pager pg={pg} noun="entries" />}
    >
      {log.err && !log.data ? (
        <div className="p-5">
          <ErrorBox>{log.err}</ErrorBox>
        </div>
      ) : !log.data ? (
        <Quiet>Loading…</Quiet>
      ) : !lines.length ? (
        <Quiet>{log.data.lines.length ? "No entries match this search." : "No entries in this period."}</Quiet>
      ) : (
        <ByDay
          items={pg.rows}
          render={(l, key) => (
            <Row
              key={key}
              mark="!"
              title={<span className="line-clamp-1 break-all">{l.message.split("\n")[0]}</span>}
              meta={`${clock(l.at)} · ${l.level.toLowerCase()}`}
              detail={<Pre tone="plain">{l.message}</Pre>}
              open={open === key}
              onToggle={() => setOpen(open === key ? null : key)}
            />
          )}
        />
      )}
    </Card>
  );
}
