"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApprovalWaiting, CustomDetail } from "@/lib/api";
import { Empty, ErrorBox, Spinner } from "../bits";
import { Abilities, ReleasePill, Rules, TestResults } from "./parts";

/** Agents waiting for this person's approval. Anyone can be asked, not only admins: they read what
 *  the agent does and its test answers, try the exact version, then approve or send it back. */
export function Approvals({ onTry, onChanged }: { onTry: (key: string) => void; onChanged: () => void }) {
  const [items, setItems] = useState<ApprovalWaiting[] | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [done, setDone] = useState("");

  const load = useCallback(() => {
    api
      .approvals()
      .then((r) => setItems(r.items))
      .catch((e) => {
        setItems([]);
        setErr(e.message);
      });
  }, []);
  useEffect(load, [load]);

  if (open) {
    return (
      <Decide
        id={open}
        onBack={() => setOpen(null)}
        onTry={onTry}
        onDecided={(msg) => {
          setOpen(null);
          setDone(msg);
          load();
          onChanged();
        }}
      />
    );
  }
  return (
    <div>
      {done ? (
        <p className="mb-4 rounded-lg px-3 py-2 text-[14px]" style={{ background: "var(--brand-soft)" }} role="status">
          {done}
        </p>
      ) : null}
      <ErrorBox>{err}</ErrorBox>
      {items === null ? (
        <Spinner label="Loading…" />
      ) : items.length === 0 ? (
        <Empty title="Nothing is waiting for you" hint="When someone asks you to approve an agent, it appears here." />
      ) : (
        <ul className="grid gap-3">
          {items.map((i) => (
            <li key={i.id}>
              <button type="button" className="card flex w-full flex-wrap items-center gap-3 p-5 text-left transition hover:border-[var(--brand)]" onClick={() => setOpen(i.id)}>
                <span className="min-w-0 flex-1">
                  <span className="block text-[16px] font-semibold">{i.name}</span>
                  <span className="block text-[13.5px] muted">
                    Version {i.version}
                    {i.replaces ? ` (replaces version ${i.replaces}, which is live)` : ""} · sent by {i.sent_by}
                  </span>
                  {i.note ? <span className="mt-1 block text-[14px]">“{i.note}”</span> : null}
                </span>
                <ReleasePill status="waiting" label="Waiting for you" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Decide({ id, onBack, onTry, onDecided }: { id: string; onBack: () => void; onTry: (key: string) => void; onDecided: (msg: string) => void }) {
  const [d, setD] = useState<CustomDetail | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.approvalGet(id).then(setD).catch((e) => setErr(e.message));
  }, [id]);

  async function decide(approve: boolean) {
    setBusy(true);
    setErr("");
    try {
      await api.approvalDecide(id, approve, note.trim());
      onDecided(approve ? `“${d?.name}” is approved and now live for the people chosen.` : `“${d?.name}” was sent back with your note.`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!d) return err ? <ErrorBox>{err}</ErrorBox> : <Spinner label="Loading…" />;
  const sub = d.meta.submitted;
  return (
    <div className="w-full">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <button type="button" className="btn btn-quiet" onClick={onBack}>
          ← Back
        </button>
        <h2 className="min-w-0 break-words text-xl font-semibold">{d.name}</h2>
        <span className="text-sm muted">Version {d.latest.version}</span>
      </div>
      <ErrorBox>{err}</ErrorBox>
      <div className="card p-5">
        <p className="text-[14px]">
          <b>{sub?.by}</b> asks you to approve this version{d.live_version ? `. It would replace version ${d.live_version}, which people use now` : ""}.
        </p>
        {sub?.note ? <p className="mt-1 text-[14px]">“{sub.note}”</p> : null}
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" className="btn btn-quiet" onClick={() => onTry(d.key)}>
            Try this version
          </button>
        </div>
      </div>

      <div className="card mt-4 p-5">
        <h3 className="text-[15px] font-semibold">What it does</h3>
        <p className="mt-1 text-[14px] muted">{d.spec.description}</p>
        <details className="mt-3">
          <summary className="cursor-pointer text-[13px] font-medium muted">Its instructions</summary>
          <p className="mt-2 whitespace-pre-wrap text-[14px]">{d.spec.instructions}</p>
        </details>
        <h4 className="mt-4 text-[13px] font-semibold uppercase tracking-wide faint">Rules it must follow</h4>
        <div className="mt-1">
          <Rules spec={d.spec} />
        </div>
        <h4 className="mt-4 text-[13px] font-semibold uppercase tracking-wide faint">What it uses</h4>
        <div className="mt-2">
          <Abilities spec={d.spec} />
        </div>
      </div>

      <div className="card mt-4 p-5">
        <h3 className="text-[15px] font-semibold">Test answers</h3>
        <div className="mt-2">{d.test ? <TestResults run={d.test} /> : <p className="text-[14px] muted">No test results.</p>}</div>
      </div>

      <div className="card mt-4 p-5">
        <h3 className="text-[15px] font-semibold">Your decision</h3>
        <label className="mt-2 block">
          <span className="text-[13px] font-medium muted">Note (needed if you send it back)</span>
          <textarea className="field mt-1 min-h-[80px]" value={note} onChange={(e) => setNote(e.target.value)} maxLength={600} />
        </label>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => decide(true)}>
            {busy ? "Saving…" : "Approve: make it live"}
          </button>
          <button type="button" className="btn btn-quiet" disabled={busy || note.trim().length < 3} onClick={() => decide(false)}>
            Send back for changes
          </button>
        </div>
        <p className="help">
          Approving makes this version available to: {d.spec.audience.map((a) => a.principal).join(", ") || "nobody yet (the builder chooses)"}.
        </p>
      </div>
    </div>
  );
}
