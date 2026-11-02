"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, CustomDetail, TestJob } from "@/lib/api";
import { Access, AccessStep } from "../BuilderParts";
import { ErrorBox, Notice, Select, Spinner } from "../bits";
import { Dialog } from "../deployer/parts";
import { CloseIcon } from "../icons";
import { Abilities, ReleasePill, ReleaseSteps, Rules, TestResults } from "./parts";

/** One custom agent for its builders: where its release is up to, and the one next thing to do. */
export function CustomAgentPage({
  id,
  onBack,
  onEdit,
  onTry,
}: {
  id: string;
  onBack: () => void;
  onEdit: (d: CustomDetail) => void;
  onTry: (key: string) => void;
}) {
  const [d, setD] = useState<CustomDetail | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [job, setJob] = useState<TestJob | null>(null);
  const [send, setSend] = useState(false);
  const [people, setPeople] = useState(false);
  const [confirm, setConfirm] = useState<"" | "retire" | "delete">("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      setD(await api.customGet(id));
    } catch (e: any) {
      setErr(e.message);
    }
  }, [id]);

  useEffect(() => {
    load();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  // While the documents are being read, check again every few seconds.
  const reading = d?.latest.meta.docs?.state === "reading";
  useEffect(() => {
    if (!reading) return;
    const t = setTimeout(load, 5000);
    return () => clearTimeout(t);
  }, [reading, d, load]);

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setErr("");
    try {
      await fn();
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  async function runTests() {
    setErr("");
    try {
      const { job: jid } = await api.customTest(id);
      const poll = async () => {
        try {
          const j = await api.customJob(jid);
          setJob(j);
          if (j.state === "running") timer.current = setTimeout(poll, 2500);
          else {
            if (j.state === "failed") setErr(j.error || "The tests could not run.");
            await load();
          }
        } catch (e: any) {
          setErr(e.message);
          setJob(null);
        }
      };
      setJob({ state: "running", done: 0, total: 0, step: "Starting", seconds: 0 });
      poll();
    } catch (e: any) {
      setErr(e.message);
    }
  }

  if (!d) return err ? <ErrorBox>{err}</ErrorBox> : <Spinner label="Loading…" />;

  const st = d.latest.status;
  const m = d.latest.meta;
  const testing = job?.state === "running";
  const neverLive = d.versions.every((v) => v.status === "draft" || v.status === "waiting");
  const tryKey = st === "live" ? "ca:" + d.id : d.key;

  return (
    <div className="w-full">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <button type="button" className="btn btn-quiet" onClick={onBack}>
          ← Back
        </button>
        <h2 className="min-w-0 break-words text-xl font-semibold">{d.name}</h2>
        <ReleasePill status={st} label={d.latest.status_label} />
        <span className="text-sm muted">Version {d.latest.version}</span>
        {d.live_version && d.live_version !== d.latest.version ? (
          <span className="text-sm muted">· version {d.live_version} is live and keeps working until this one is approved</span>
        ) : null}
      </div>

      <ErrorBox>{err}</ErrorBox>
      {(d.warnings || []).map((w) => (
        <Notice key={w}>{w}</Notice>
      ))}
      {m.decision && !m.decision.approved && st === "draft" ? (
        <div className="notice mb-3">
          <b>{m.decision.by} sent it back:</b> {m.decision.note}
        </div>
      ) : null}

      <div className="card p-5">
        <ReleaseSteps s={d} />
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {st === "draft" ? (
            <>
              <button type="button" className="btn btn-primary" disabled={!d.ready_to_send} onClick={() => setSend(true)}>
                Send for approval
              </button>
              <button type="button" className="btn btn-quiet" disabled={testing || reading} onClick={runTests}>
                {testing ? "Testing…" : m.test ? "Run the tests again" : "Run the tests"}
              </button>
            </>
          ) : null}
          <button type="button" className="btn btn-quiet" disabled={reading} onClick={() => onTry(tryKey)}>
            {st === "live" ? "Open it" : "Try it"}
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => onEdit(d)}>
            {st === "live" || st === "retired" ? "Change (makes a new version)" : st === "waiting" ? "Change (takes it back)" : "Change"}
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => setPeople(true)}>
            Who can use it
          </button>
          {d.live_version ? (
            <button type="button" className="btn btn-quiet ml-auto" onClick={() => setConfirm("retire")}>
              Retire
            </button>
          ) : neverLive ? (
            <button type="button" className="btn btn-quiet ml-auto" onClick={() => setConfirm("delete")}>
              Delete
            </button>
          ) : null}
        </div>
        {st === "draft" && d.next ? <p className="help">{reading ? "Reading the documents first." : d.next}</p> : null}
        {st === "waiting" ? (
          <p className="help">
            Sent by {m.submitted?.by} to {(m.submitted?.approvers || []).join(", ")}. Any one of them can approve it, or send it back.
          </p>
        ) : null}
        {confirm ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg px-3 py-2" style={{ background: "var(--warn-bg)" }}>
            <span className="text-[14px]">
              {confirm === "retire"
                ? "Retire it? Nobody will be able to use it any more. Its history is kept."
                : "Delete this draft for good?"}
            </span>
            <button
              type="button"
              className="btn btn-quiet"
              disabled={!!busy}
              onClick={() =>
                act(confirm, async () => {
                  if (confirm === "retire") await api.customRetire(id);
                  else {
                    await api.customDelete(id);
                    onBack();
                  }
                  setConfirm("");
                })
              }
            >
              Yes, {confirm}
            </button>
            <button type="button" className="btn btn-quiet" onClick={() => setConfirm("")}>
              No
            </button>
          </div>
        ) : null}
      </div>

      {m.docs && m.docs.state !== "none" ? (
        <div className="card mt-4 p-5">
          <h3 className="text-[15px] font-semibold">Documents</h3>
          {m.docs.state === "reading" ? (
            <div className="mt-2">
              <Spinner label="Reading the documents. This can take a few minutes for big folders." />
            </div>
          ) : (
            <p className="mt-1 text-[14px]">
              {m.docs.state === "ready"
                ? `Read ${m.docs.files} ${m.docs.files === 1 ? "file" : "files"}. The agent knows what each one is:`
                : "The documents could not be read."}
            </p>
          )}
          {m.docs.state === "ready" && (m.docs.outline || []).length ? (
            <ul className="mt-2 space-y-1 text-[13.5px]">
              {(m.docs.outline || []).map((o) => (
                <li key={o.file} className="break-words">
                  <b>{o.file.split("/").pop()}</b> <span className="muted">· {o.about}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {(m.docs.problems || []).length ? (
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[13px] muted">
              {(m.docs.problems || []).map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div className="card mt-4 p-5">
        <h3 className="text-[15px] font-semibold">Tests</h3>
        {testing && job ? (
          <div className="mt-2 space-y-1" role="status">
            <Spinner label={job.total ? `Question ${Math.min(job.done + 1, job.total)} of ${job.total}` : "Starting"} />
            {job.step ? <p className="line-clamp-2 text-[13px] muted">{job.step}</p> : null}
            <p className="help">Each answer is given for real and checked by another model. This takes a minute or two.</p>
          </div>
        ) : d.test ? (
          <div className="mt-2">
            <TestResults
              run={d.test}
              onAccept={st === "draft" ? async (i, note) => {
                await api.customAccept(id, d.test!.id, i, note);
                await load();
              } : undefined}
            />
          </div>
        ) : (
          <p className="mt-1 text-[14px] muted">Not tested yet. Its {d.spec.tests.length} test questions, and one for each rule, are asked when you run the tests.</p>
        )}
      </div>

      <div className="card mt-4 p-5">
        <h3 className="text-[15px] font-semibold">What it does</h3>
        <p className="mt-1 text-[14px] muted">{d.spec.description}</p>
        <details className="mt-3">
          <summary className="cursor-pointer text-[13px] font-medium muted">Instructions</summary>
          <p className="mt-2 whitespace-pre-wrap text-[14px]">{d.spec.instructions}</p>
        </details>
        <h4 className="mt-4 text-[13px] font-semibold uppercase tracking-wide faint">Rules</h4>
        <div className="mt-1">
          <Rules spec={d.spec} />
        </div>
        <h4 className="mt-4 text-[13px] font-semibold uppercase tracking-wide faint">Uses</h4>
        <div className="mt-2">
          <Abilities spec={d.spec} />
        </div>
      </div>

      <div className="card mt-4 p-5">
        <h3 className="text-[15px] font-semibold">Versions</h3>
        <ul className="mt-2 divide-y" style={{ borderColor: "var(--line)" }}>
          {d.versions.map((v) => (
            <li key={v.version} className="flex flex-wrap items-center gap-3 py-2 text-[14px]">
              <span className="w-24 font-medium">Version {v.version}</span>
              <ReleasePill status={v.status} label={v.status_label} />
              <span className="muted">
                by {v.by}
                {v.live_since ? ` · live since ${v.live_since.slice(0, 10)}` : ""}
                {v.decision ? ` · ${v.decision.approved ? "approved" : "sent back"} by ${v.decision.by}` : ""}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {send ? <SendDialog id={id} name={d.name} onClose={() => setSend(false)} onDone={() => { setSend(false); load(); }} /> : null}
      {people ? (
        <PeopleDialog
          id={id}
          initial={d.spec.audience as Access[]}
          onClose={() => setPeople(false)}
          onDone={() => {
            setPeople(false);
            load();
          }}
        />
      ) : null}
    </div>
  );
}

function SendDialog({ id, name, onClose, onDone }: { id: string; name: string; onClose: () => void; onDone: () => void }) {
  const [users, setUsers] = useState<{ name: string; display: string }[] | null>(null);
  const [pick, setPick] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    api
      .builderPrincipals()
      .then((p) => setUsers(p.users))
      .catch((e) => {
        setUsers([]);
        setErr(e.message);
      });
  }, []);
  const label = (u: string) => users?.find((x) => x.name === u)?.display || u;
  return (
    <Dialog
      wide
      title={`Send “${name}” for approval`}
      sub="Choose who should approve it. Any one of them can approve it or send it back. It must be someone other than you."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-quiet" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!chosen.length || busy}
            onClick={async () => {
              setBusy(true);
              setErr("");
              try {
                await api.customSubmit(id, chosen, note.trim());
                onDone();
              } catch (e: any) {
                setErr(e.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Sending…" : "Send for approval"}
          </button>
        </>
      }
    >
      <ErrorBox>{err}</ErrorBox>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-[240px] flex-1">
          <span className="text-[13px] font-medium muted">Approver</span>
          <Select
            className="mt-1"
            value={pick}
            onChange={setPick}
            loading={users === null}
            placeholder="Choose a person…"
            options={(users || []).map((u) => ({
              value: u.name,
              label: u.display || u.name,
              detail: u.display && u.display !== u.name ? u.name : undefined,
              ...(chosen.includes(u.name) ? { disabled: true, note: "Added" } : {}),
            }))}
          />
        </label>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={!pick}
          onClick={() => {
            setChosen([...chosen, pick]);
            setPick("");
          }}
        >
          Add
        </button>
      </div>
      <ul className="mt-3 flex flex-wrap gap-2" aria-label="Chosen approvers">
        {chosen.map((u) => (
          <li key={u} className="tag !min-h-[34px] gap-2 !text-sm">
            {label(u)}
            <button type="button" aria-label={`Remove ${u}`} onClick={() => setChosen(chosen.filter((x) => x !== u))}>
              <CloseIcon size={12} />
            </button>
          </li>
        ))}
      </ul>
      <label className="mt-4 block">
        <span className="text-[13px] font-medium muted">A note for them (optional)</span>
        <textarea className="field mt-1 min-h-[80px]" value={note} onChange={(e) => setNote(e.target.value)} maxLength={600} />
      </label>
      <p className="help">They see it under Approvals, can try it, and read the test answers before deciding.</p>
    </Dialog>
  );
}

function PeopleDialog({ id, initial, onClose, onDone }: { id: string; initial: Access[]; onClose: () => void; onDone: () => void }) {
  const [access, setAccess] = useState<Access[]>(initial);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  return (
    <Dialog
      wide
      title="Who can use it"
      sub="Changing this does not need a new test or approval. People get it only while a version is live."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-quiet" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setErr("");
              try {
                await api.customAudience(id, access);
                onDone();
              } catch (e: any) {
                setErr(e.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <ErrorBox>{err}</ErrorBox>
      <AccessStep access={access} setAccess={setAccess} note="They can use it, but not change it." />
    </Dialog>
  );
}
