"use client";

import { useEffect, useMemo, useState } from "react";
import { Agent, api, AppCandidate } from "@/lib/api";
import { Access, AccessStep, Finished, Section, VolumeField, WizardFrame } from "./BuilderParts";
import { ErrorBox, Select, Spinner } from "./bits";

/** Offer an agent someone wrote in code (running as its own Databricks App) to the
 *  people who use the portal. Nothing in the app is changed: the portal records its
 *  name and address, is given Can Manage on it so it can read who may use it, and
 *  gives the chosen teams Can Use. The person's own sign-in is what calls it. */

const DEFAULT_PATH = "/invocations";
const PATH_RE = /^\/[A-Za-z0-9_\-./]{0,200}$/;

type Tried = { ok: boolean; reply?: string; message?: string; seconds?: number; tools?: string[] } | null;

export function AppAgentWizard({
  editing,
  onCancel,
  onFinished,
}: {
  editing: Agent | null;
  onCancel: () => void;
  onFinished: (d: Finished) => void;
}) {
  const steps = useMemo(
    () =>
      [
        { key: "app", title: "Choose the app" },
        { key: "name", title: "Name it" },
        { key: "files", title: "Files (optional)" },
        ...(editing ? [] : [{ key: "access", title: "Who can use it" }]),
        { key: "review", title: "Review" },
      ] as { key: string; title: string }[],
    [editing]
  );
  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(0);
  const [apps, setApps] = useState<AppCandidate[] | null>(editing ? [] : null);
  const [appsErr, setAppsErr] = useState("");
  const [appName, setAppName] = useState(editing?.app_name || "");
  const [path, setPath] = useState(editing?.path || DEFAULT_PATH);
  const [name, setName] = useState(editing?.display_name || "");
  const [blurb, setBlurb] = useState(editing?.blurb || "");
  const [upload, setUpload] = useState(editing?.upload_volume || "");
  const [accepts, setAccepts] = useState((editing?.accepts || []).join(", "));
  const [output, setOutput] = useState(editing?.output_volume || "");
  const [access, setAccess] = useState<Access[]>([]);
  const [tried, setTried] = useState<Tried>(null);
  const [trying, setTrying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (editing) return;
    api
      .appAgentApps()
      .then((r) => setApps(r.apps))
      .catch((e) => {
        setApps([]);
        setAppsErr(e.message);
      });
  }, [editing]);

  const chosen = (apps || []).find((a) => a.name === appName);
  useEffect(() => {
    // A sensible starting name, which the admin can change.
    if (!editing && chosen && !name) setName(chosen.name.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()));
    setTried(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appName]);

  const pathOk = PATH_RE.test(path) && !path.includes("..");
  const key = steps[step].key;
  const problem =
    key === "app"
      ? !appName
        ? "Choose the app first."
        : !pathOk
          ? "The address must start with /, for example /invocations."
          : ""
      : key === "name"
        ? !name.trim()
          ? "Give it a name people will recognise."
          : ""
        : "";

  function go(i: number) {
    setStep(i);
    setReached((r) => Math.max(r, i));
  }

  async function tryIt() {
    setTrying(true);
    setTried(null);
    try {
      setTried(await api.appAgentTry({ app_name: appName, path }));
    } catch (e: any) {
      setTried({ ok: false, message: e.message });
    } finally {
      setTrying(false);
    }
  }

  async function finish() {
    setBusy(true);
    setErr("");
    const spec = { display_name: name.trim(), blurb: blurb.trim(), path, upload_volume: upload, accepts, output_volume: output };
    try {
      if (editing) {
        await api.appAgentSave(appName, spec);
        onFinished({ kind: "saved", name: spec.display_name, variant: "app" });
      } else {
        const r = await api.appAgentAdd({ app_name: appName, ...spec, share: access });
        onFinished({ kind: "created", name: spec.display_name, variant: "app", warnings: r.warnings, access: access.length });
      }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setErr("");
    try {
      await api.appAgentRemove(appName);
      onFinished({ kind: "deleted", name: name || appName, variant: "app" });
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const appOptions = (apps || []).map((a) => ({
    value: a.name,
    label: a.name,
    detail: [a.running ? "Running" : "Not running (" + a.state + ")", a.description].filter(Boolean).join(" · "),
    ...(a.registered ? { disabled: true, note: "Already added" } : {}),
  }));

  return (
    <WizardFrame
      title={editing ? `Change “${editing.display_name}”` : "Add an agent built in code"}
      steps={steps}
      step={step}
      reached={reached}
      editing={!!editing}
      onGo={go}
      err={err}
      problem={problem}
      busy={busy}
      finishLabel={editing ? "Save changes" : "Add it to the portal"}
      finishDisabled={!!problem || !appName || !name.trim()}
      onFinish={finish}
      onCancel={onCancel}
      onDelete={editing ? remove : undefined}
      deleteLabel="Remove from the portal"
    >
      {key === "app" ? (
        <div className="space-y-6">
          <div>
            <h3 className="text-lg font-semibold">Which app is the agent?</h3>
            <p className="mt-1 text-[15px] muted">
              An agent written in code (for example with LangGraph or the Agent Bricks tools) runs as its own app in Databricks.
              Choose it here and people can chat with it in the portal like any other assistant. The app itself is not changed.
            </p>
          </div>
          {editing ? (
            <p className="rounded-xl p-4 text-[15px]" style={{ background: "var(--canvas)" }}>
              <b>{appName}</b>
              {editing.app_url ? <span className="block break-all text-[13px] muted">{editing.app_url}</span> : null}
            </p>
          ) : (
            <label className="block">
              <span className="label">App</span>
              <Select
                className="mt-1"
                value={appName}
                onChange={setAppName}
                options={appOptions}
                loading={apps === null}
                placeholder="Choose an app…"
                empty="No apps found in this workspace"
              />
              {appsErr ? <span className="help">Could not list the apps ({appsErr.slice(0, 160)}).</span> : null}
              {chosen && !chosen.running ? (
                <span className="mt-2 block rounded-lg px-3 py-2 text-[13px]" style={{ background: "var(--warn-bg)" }} role="status">
                  This app is not running ({chosen.state}). You can still add it; people can use it once someone starts it in
                  Databricks.
                </span>
              ) : null}
            </label>
          )}

          <details open={path !== DEFAULT_PATH}>
            <summary className="cursor-pointer text-[13px] font-medium muted">Advanced: the address it answers questions on</summary>
            <label className="mt-2 block">
              <input className="field font-mono !text-[13px]" value={path} onChange={(e) => setPath(e.target.value.trim())} placeholder={DEFAULT_PATH} />
              <span className="help">
                Most agents built with Databricks answer on {DEFAULT_PATH}. Some hand-written ones use another, such as /api/chat. The
                portal works out the message format by itself.
              </span>
            </label>
          </details>

          <div className="rounded-xl p-5" style={{ border: "1px solid var(--line)", background: "var(--canvas)" }}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <h4 className="text-[15px] font-semibold">Check that it answers</h4>
                <p className="text-[13.5px] muted">Sends one short hello as you, so you know the portal and the app understand each other.</p>
              </div>
              <button type="button" className="btn btn-quiet" onClick={tryIt} disabled={!appName || !pathOk || trying}>
                {trying ? "Asking…" : "Send a test message"}
              </button>
            </div>
            {trying ? <div className="mt-3"><Spinner label="Waiting for the app to answer…" /></div> : null}
            {tried ? (
              tried.ok ? (
                <div className="mt-3 rounded-lg px-3 py-2 text-[14px]" style={{ background: "var(--brand-soft)" }} role="status">
                  <b>It answered</b>
                  {tried.seconds != null ? <span className="muted"> in {tried.seconds} s</span> : null}
                  <span className="mt-1 block max-h-48 overflow-auto whitespace-pre-wrap break-words text-[13.5px]">{tried.reply || "(an empty answer)"}</span>
                </div>
              ) : (
                <div className="mt-3">
                  <ErrorBox>{tried.message || "It did not answer."}</ErrorBox>
                </div>
              )
            ) : null}
          </div>
        </div>
      ) : null}

      {key === "name" ? (
        <div className="space-y-5">
          <h3 className="text-lg font-semibold">What should people see?</h3>
          <label className="block">
            <span className="label">Name</span>
            <input className="field mt-1" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="Media planner" />
          </label>
          <label className="block">
            <span className="label">What it helps with</span>
            <textarea
              className="field mt-1 min-h-[96px]"
              value={blurb}
              onChange={(e) => setBlurb(e.target.value)}
              maxLength={600}
              placeholder="One or two sentences, shown on its card."
            />
          </label>
        </div>
      ) : null}

      {key === "files" ? (
        <div className="space-y-6">
          <div>
            <h3 className="text-lg font-semibold">Does it work with files?</h3>
            <p className="mt-1 text-[15px] muted">
              Skip this if it does not. If it reads files people attach, choose where those go. Files it names in an answer can always be
              downloaded by the person who asked.
            </p>
          </div>
          <VolumeField
            label="Where attached files go"
            hint="People need permission to save to this folder."
            value={upload}
            suffix=""
            onChange={setUpload}
          />
          <label className="block">
            <span className="label">File types it accepts</span>
            <input className="field mt-1" value={accepts} onChange={(e) => setAccepts(e.target.value)} placeholder="xlsx, csv, pdf (empty: any)" />
          </label>
          <VolumeField
            label="Where it saves results (optional)"
            hint="Told to the agent with each question that has a file."
            value={output}
            suffix=""
            onChange={setOutput}
          />
        </div>
      ) : null}

      {key === "access" ? (
        <AccessStep
          access={access}
          setAccess={setAccess}
          note="They can use it, but not change it. This is given on the app itself in Databricks, straight away."
        />
      ) : null}

      {key === "review" ? (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold">Check and {editing ? "save" : "add"}</h3>
          <Section title="App" onChange={() => go(0)}>
            <p className="text-[15px] font-medium">{appName}</p>
            <p className="text-[13px] muted">Answers on {path}</p>
            {tried?.ok ? <p className="text-[13px]" style={{ color: "var(--ok)" }}>Answered the test message</p> : null}
          </Section>
          <Section title="Name" onChange={() => go(1)}>
            <p className="text-[15px] font-medium">{name}</p>
            <p className="text-[14px] muted">{blurb || "No description"}</p>
          </Section>
          <Section title="Files" onChange={() => go(2)}>
            <p className="text-[14px] muted">
              {upload ? `Attached files go to ${upload}${accepts ? ` (${accepts})` : ""}.` : "People cannot attach files."}
              {output ? ` Results go to ${output}.` : ""}
            </p>
          </Section>
          {!editing ? (
            <Section title="Who can use it" onChange={() => go(3)}>
              <p className="text-[14px] muted">
                {access.length ? access.map((a) => a.principal).join(", ") : "Nobody new. Whoever can already use the app in Databricks sees it."}
              </p>
            </Section>
          ) : null}
          <p className="help">
            The portal is given Can Manage on the app so it can read and change who may use it. People ask it questions with their own
            sign-in, so Databricks decides what they can do.
          </p>
        </div>
      ) : null}
    </WizardFrame>
  );
}
