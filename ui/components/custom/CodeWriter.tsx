"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { CodeCheck, CodeRound, CodeSetup, CodeTest, CodeTestResult, CodeWritten, CustomSummary, DesignerModel } from "@/lib/api";
import { chosen, loadChoice, modelDetail } from "@/lib/designerModels";
import { ErrorBox, Select, Spinner } from "../bits";
import { CheckIcon, DownloadIcon, PlusIcon, TrashIcon } from "../icons";
import { CodeIDE, LiveWriting } from "./CodeWorkbench";

/** Developer mode: the AI writes an agent's code (code_agent.py). Describe it; the code model writes it into the
 *  project our export makes; the developer reads the diff and approves; it is deployed as an app and tested; a
 *  failed test or a change asked in words comes back as edits to approve again. The code never runs in the portal. */

type Stage = "describe" | "writing" | "review" | "deploying" | "test";
const STAGES: [Stage, string][] = [
  ["describe", "Describe"],
  ["writing", "Write"],
  ["review", "Review"],
  ["deploying", "Deploy"],
  ["test", "Test"],
];
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const lines = (s: string) =>
  s
    .split(/[\n,]/)
    .map((x) => x.trim())
    .filter(Boolean);

export function CodeWriter({
  models,
  customs,
  onCancel,
  onDone,
}: {
  models: DesignerModel[];
  customs: CustomSummary[];
  onCancel: () => void;
  onDone: () => void;
}) {
  const [stage, setStage] = useState<Stage>("describe");
  const [setup, setSetup] = useState<CodeSetup>({
    name: "",
    description: "",
    framework: "mlflow",
    source: "",
    model: "",
    task: "",
    write_folders: [],
    websites: [],
  });
  const [folders, setFolders] = useState("");
  const [sites, setSites] = useState("");
  const [codeModel, setCodeModel] = useState("");
  const [err, setErr] = useState("");
  const [step, setStep] = useState("");
  const [notes, setNotes] = useState<string[]>([]);
  const [written, setWritten] = useState<CodeWritten | null>(null);
  const [tests, setTests] = useState<CodeTest[]>([]);
  const [read, setRead] = useState(false);
  const [change, setChange] = useState("");
  const [appName, setAppName] = useState("");
  const [deployed, setDeployed] = useState("");
  const [results, setResults] = useState<CodeTestResult[] | null>(null);
  const [testing, setTesting] = useState(false);
  // the live view, and the code as it is now (the developer may edit it), checked as they type
  const [rounds, setRounds] = useState<CodeRound[]>([]);
  const [started, setStarted] = useState(Date.now());
  const [before, setBefore] = useState<Record<string, string> | null>(null);
  const [code, setCode] = useState<Record<string, string>>({});
  const [check, setCheck] = useState<CodeCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const checkSeq = useRef(0);
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
    },
    []
  );

  const chat = models.filter((m) => m.tools !== false);
  const picks = () => chosen({ ...loadChoice(models), ...(codeModel ? { code: codeModel } : {}) });
  const set = (p: Partial<CodeSetup>) => setSetup((s) => ({ ...s, ...p }));
  const full = (): CodeSetup => ({ ...setup, write_folders: lines(folders), websites: lines(sites) });

  async function follow(job: string) {
    const t0 = Date.now();
    setRounds([]);
    setStarted(t0);
    while (alive.current && Date.now() - t0 < 20 * 60 * 1000) {
      await wait(700); // often enough that lines appear as they are written
      const j = await api.codeAgentWriteJob(job);
      if (!alive.current) return;
      setStep(j.step);
      setNotes(j.notes || []);
      setRounds(j.rounds || []);
      if (j.state === "failed") throw new Error(j.error || "The code could not be written.");
      if (j.state === "done" && j.result) {
        const r = j.result;
        setWritten(r);
        setCode(r.code);
        setCheck({ problems: r.problems, report: r.report, diff: r.diff, fingerprint: r.fingerprint });
        if (r.tests.length) setTests(r.tests);
        setRead(false);
        setStage("review");
        return;
      }
    }
    throw new Error("It took too long. Try again.");
  }

  // Hand edits are checked a moment after the typing stops (no model, nothing run); any edit needs a new approval.
  function edit(path: string, text: string) {
    setCode((c) => ({ ...c, [path]: text }));
    setRead(false);
  }
  useEffect(() => {
    if (stage !== "review" || !written) return;
    const same = Object.keys(code).every((p) => code[p] === written.code[p]) && Object.keys(code).length === Object.keys(written.code).length;
    if (same && check?.fingerprint === written.fingerprint) return;
    const seq = ++checkSeq.current;
    setChecking(true);
    const t = setTimeout(async () => {
      try {
        const c = await api.codeAgentCheck(setup, code);
        if (seq === checkSeq.current && alive.current) setCheck(c);
      } catch (e: any) {
        if (seq === checkSeq.current) setErr(e.message);
      } finally {
        if (seq === checkSeq.current) setChecking(false);
      }
    }, 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  async function writeIt() {
    setErr("");
    setBefore(null);
    setStage("writing");
    setStep("Starting");
    try {
      const s = full();
      setSetup(s);
      await follow((await api.codeAgentWrite(s, picks())).job);
    } catch (e: any) {
      setErr(e.message);
      setStage(written ? "review" : "describe");
    }
  }

  async function changeIt(request: string, failures: CodeTestResult[] = []) {
    if (!written) return;
    setErr("");
    setStage("writing");
    setStep("Making the change");
    try {
      setBefore(code);
      await follow((await api.codeAgentChange(setup, code, request, failures, picks())).job);
      setChange("");
    } catch (e: any) {
      setErr(e.message);
      setStage("review");
    }
  }

  async function deployIt() {
    if (!written) return;
    setErr("");
    setStage("deploying");
    setStep("Checking it again");
    try {
      const started = await api.codeAgentDeploy(setup, code, tests, check?.fingerprint || "", setup.name, deployed || appName.trim(), picks());
      setDeployed(started.app_name);
      for (let i = 0; i < 200 && alive.current; i++) {
        await wait(6000);
        const j = await api.codeAgentJob(started.job);
        if (!alive.current) return;
        setStep(j.step);
        if (j.state === "failed") throw new Error(j.step);
        if (j.state === "done") {
          setResults(null);
          setStage("test");
          return;
        }
      }
    } catch (e: any) {
      setErr(e.message);
      setStage("review");
    }
  }

  async function runTests() {
    setErr("");
    setTesting(true);
    setResults(null);
    try {
      const { job } = await api.codeAgentTest(deployed, tests, picks());
      for (let i = 0; i < 300 && alive.current; i++) {
        await wait(4000);
        const j = await api.codeAgentTestJob(job);
        if (!alive.current) return;
        setStep(j.step);
        if (j.state === "failed") throw new Error(j.error);
        if (j.state === "done" && j.result) {
          setResults(j.result.results);
          return;
        }
      }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setTesting(false);
    }
  }

  async function download() {
    if (!written) return;
    try {
      const { blob, file } = await api.codeAgentZip(setup, written.code, tests);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = file;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e: any) {
      setErr(e.message);
    }
  }

  const at = STAGES.findIndex(([s]) => s === stage);
  const ready = setup.name.trim().length >= 2 && setup.task.trim().length >= 20 && (setup.source || setup.model);
  const failed = (results || []).filter((r) => r.verdict !== "pass");

  return (
    <div className="max-w-4xl">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button type="button" className="btn btn-quiet" onClick={onCancel}>
          ← Cancel
        </button>
        <h2 className="text-xl font-semibold">Write an agent&apos;s code with AI</h2>
        <span className="rounded-full px-2 py-0.5 text-[11px] font-semibold" style={{ background: "var(--brand-soft)", color: "var(--brand-deep)" }}>
          Developer mode
        </span>
      </div>
      <ol className="mb-4 flex flex-wrap gap-2 text-[13px]" aria-label="Steps">
        {STAGES.map(([s, label], i) => (
          <li
            key={s}
            className="rounded-full px-3 py-1 font-medium"
            aria-current={i === at ? "step" : undefined}
            style={
              i === at
                ? { background: "var(--brand)", color: "var(--btn-ink)" }
                : { background: "var(--bubble)", color: i < at ? "var(--ink)" : "var(--ink-faint)" }
            }
          >
            {i + 1}. {label}
          </li>
        ))}
      </ol>
      <ErrorBox>{err}</ErrorBox>
      {notes.length ? <p className="notice mb-3">{notes.join(" ")}</p> : null}

      {stage === "describe" ? (
        <div className="card space-y-5 p-6">
          <p className="text-[15px] muted">
            Say what the agent should do. The AI writes its code into a tested project, you read every line before anything is
            deployed, and it runs as its own app with each person&apos;s own sign-in. The portal never runs the code itself.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="label">Name</span>
              <input className="field mt-1" value={setup.name} maxLength={80} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Media Plan Builder" />
            </label>
            <label className="block">
              <span className="label">What it does (one sentence)</span>
              <input className="field mt-1" value={setup.description} maxLength={300} onChange={(e) => set({ description: e.target.value })} />
            </label>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <span className="label">Start from</span>
              <Select
                className="mt-1"
                value={setup.source}
                onChange={(v) => set({ source: v })}
                ariaLabel="Start from"
                options={[
                  { value: "", label: "Just an AI model", detail: "No tools or data yet: the code does the work" },
                  ...customs.map((c) => ({ value: c.id, label: c.name, detail: "Uses its documents, tools, data and runbook" })),
                ]}
              />
              <span className="help">A custom agent gives the code its documents, tools and data to call.</span>
            </div>
            <div>
              <span className="label">AI model the agent uses{setup.source ? " (optional)" : ""}</span>
              <Select
                className="mt-1"
                value={setup.model}
                onChange={(v) => set({ model: v })}
                ariaLabel="AI model the agent uses"
                placeholder={setup.source ? "The custom agent's own model" : "Choose a model"}
                options={chat.map((m) => ({ value: m.name, label: m.label, detail: modelDetail(m) }))}
              />
            </div>
          </div>
          <div>
            <span className="label">How it is built</span>
            <div className="seg mt-1" role="group" aria-label="How it is built">
              <button type="button" aria-pressed={setup.framework === "mlflow"} onClick={() => set({ framework: "mlflow" })}>
                Databricks standard (MLflow)
              </button>
              <button type="button" aria-pressed={setup.framework === "langgraph"} onClick={() => set({ framework: "langgraph" })}>
                LangGraph
              </button>
            </div>
          </div>
          <label className="block">
            <span className="label">What it should do</span>
            <textarea
              className="field mt-1 min-h-[150px]"
              value={setup.task}
              maxLength={6000}
              onChange={(e) => set({ task: e.target.value })}
              placeholder="e.g. Builds a media plan from a brief: reads the ad book, picks packages within the budget, works out the margin with our rules (never below 30%), and saves the plan as an Excel file."
            />
            <span className="help">Be specific about the steps, the rules and what it must ask for. You can ask for changes after.</span>
          </label>
          <details className="rounded-xl border p-4" style={{ borderColor: "var(--line)" }}>
            <summary className="cursor-pointer text-[14px] font-semibold">What else it may reach (optional)</summary>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="text-[13px] font-medium muted">Folders it may save files into</span>
                <textarea
                  className="field mt-1 font-mono text-[13px]"
                  rows={2}
                  value={folders}
                  onChange={(e) => setFolders(e.target.value)}
                  placeholder="/Volumes/catalog/schema/volume/plans"
                />
                <span className="help">One per line. It never overwrites a file.</span>
              </label>
              <label className="block">
                <span className="text-[13px] font-medium muted">Websites it may call</span>
                <textarea className="field mt-1 font-mono text-[13px]" rows={2} value={sites} onChange={(e) => setSites(e.target.value)} placeholder="api.example.com" />
                <span className="help">Empty: no website calls.</span>
              </label>
              <div className="sm:col-span-2">
                <span className="text-[13px] font-medium muted">Code written by</span>
                <Select
                  className="mt-1 max-w-md"
                  value={codeModel}
                  onChange={setCodeModel}
                  ariaLabel="Code written by"
                  placeholder="The model chosen for writing code"
                  options={models.map((m) => ({ value: m.name, label: m.label, detail: modelDetail(m) }))}
                />
              </div>
            </div>
          </details>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className="btn btn-primary btn-lg" disabled={!ready} onClick={writeIt}>
              Write the code
            </button>
            <span className="text-[13px] faint">Takes a minute or two. Each agent is one Databricks App (a workspace has 100).</span>
          </div>
        </div>
      ) : null}

      {stage === "writing" ? <LiveWriting rounds={rounds} step={step} started={started} current={before} /> : null}
      {stage === "deploying" ? (
        <div className="card p-6">
          <Spinner label={`${step || "Working"}…`} />
          <p className="help">Deploying it as its own app. A new version takes about a minute.</p>
        </div>
      ) : null}

      {stage === "review" && written ? (
        <div className="space-y-4">
          <div className="card p-6">
            <h3 className="text-[17px] font-semibold">What the AI wrote</h3>
            {written.summary ? <p className="mt-1 text-[15px]">{written.summary}</p> : null}
            {written.problems.length && check?.problems.length ? (
              <div className="mt-3">
                <ErrorBox>
                  It does not pass the checks yet:
                  <ul className="mt-1 list-disc pl-5">
                    {written.problems.map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                </ErrorBox>
                <button type="button" className="btn btn-quiet mt-2" onClick={() => changeIt("Fix these problems: " + written.problems.join(" | "))}>
                  Ask the AI to fix them
                </button>
              </div>
            ) : null}
            <h4 className="mt-5 text-[14px] font-semibold">What it can do and reach</h4>
            <ul className="mt-2 space-y-1.5 text-[14px]">
              {(check?.report || written.report).abilities.map((a) => (
                <li key={a.name}>
                  <span className="font-mono text-[13px]">{a.name}</span> <span className="muted">{a.about}</span>
                </li>
              ))}
              {!(check?.report || written.report).abilities.length ? <li className="muted">No abilities of its own: it only uses what it was given.</li> : null}
              {setup.source ? <li className="muted">Everything the custom agent it starts from uses (documents, tools, data), as the person asking.</li> : null}
              <li className="muted">
                Saves files into: {(check?.report || written.report).write_folders.join(", ") || "nowhere"} · Websites:{" "}
                {(check?.report || written.report).websites.join(", ") || "none"}
                {(check?.report || written.report).packages.length ? ` · Extra libraries: ${(check?.report || written.report).packages.join(", ")}` : ""}
              </li>
            </ul>
          </div>

          <CodeIDE code={code} written={written.code} fixed={written.fixed || {}} check={check} checking={checking} onEdit={edit} />

          <div className="card p-6">
            <h3 className="text-[17px] font-semibold">Change it</h3>
            <p className="mt-1 text-[13px] muted">
              Edit the code above yourself, or ask the AI in words. Either way it is checked again, and you approve it again before it
              is deployed.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button type="button" className="btn btn-quiet" onClick={download}>
                <DownloadIcon size={15} /> Download the project
              </button>
            </div>
            <label className="mt-4 block">
              <span className="text-[13px] font-medium muted">Ask for a change</span>
              <textarea
                className="field mt-1"
                rows={2}
                value={change}
                maxLength={1500}
                onChange={(e) => setChange(e.target.value)}
                placeholder="e.g. Round the margin to two decimals and refuse plans under 10,000."
              />
            </label>
            <button type="button" className="btn btn-quiet mt-2" disabled={change.trim().length < 5} onClick={() => changeIt(change.trim())}>
              Make this change
            </button>
          </div>

          <TestsEditor tests={tests} onChange={setTests} />

          <div className="card p-6">
            <h3 className="text-[17px] font-semibold">{deployed ? `Deploy the new version of ${deployed}` : "Deploy it"}</h3>
            {!deployed ? (
              <label className="mt-3 block max-w-sm">
                <span className="text-[13px] font-medium muted">App name (optional)</span>
                <input
                  className="field mt-1 font-mono text-[13px]"
                  value={appName}
                  maxLength={30}
                  onChange={(e) => setAppName(e.target.value.toLowerCase())}
                  placeholder="made from the name"
                />
              </label>
            ) : null}
            <label className="mt-4 flex cursor-pointer items-start gap-2.5">
              <input type="checkbox" className="mt-1 h-4 w-4" checked={read} onChange={(e) => setRead(e.target.checked)} />
              <span className="text-[14px]">I have read the code and what it can reach, and I approve it.</span>
            </label>
            <button
              type="button"
              className="btn btn-primary btn-lg mt-4"
              disabled={!read || checking || !check || check.problems.length > 0}
              onClick={deployIt}
            >
              Deploy and test
            </button>
            {check && check.problems.length ? <p className="help">Fix the problems in the code first.</p> : null}
          </div>
        </div>
      ) : null}

      {stage === "test" ? (
        <div className="space-y-4">
          <div className="card p-6">
            <p className="flex items-center gap-2 text-[15px]">
              <span style={{ color: "var(--ok)" }} aria-hidden>
                <CheckIcon size={18} />
              </span>
              <b>{deployed} is running and offered as an assistant.</b>
            </p>
            <p className="mt-1 text-[14px] muted">Only you can use it until you share it on the Access page. Now try it on its tests.</p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button type="button" className="btn btn-primary" disabled={testing || !tests.length} onClick={runTests}>
                {testing ? `${step || "Asking"}…` : results ? "Run the tests again" : "Run the tests"}
              </button>
              <button type="button" className="btn btn-quiet" onClick={() => setStage("review")}>
                Back to the code
              </button>
              <button type="button" className="btn btn-quiet" onClick={onDone}>
                Done
              </button>
            </div>
            <p className="help">Each answer is marked by a different AI model. It checks the answers are sensible, not that every number is right.</p>
          </div>
          {results ? (
            <div className="card p-6">
              <h3 className="text-[17px] font-semibold">
                {results.length - failed.length} of {results.length} passed
              </h3>
              <ul className="mt-3 space-y-3">
                {results.map((r, i) => (
                  <li key={i} className="rounded-xl border p-3" style={{ borderColor: "var(--line)" }}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className="rounded-full px-2 text-[12px] font-semibold"
                        style={{
                          background: r.verdict === "pass" ? "color-mix(in srgb, var(--ok) 16%, transparent)" : "color-mix(in srgb, var(--err) 14%, transparent)",
                        }}
                      >
                        {r.verdict === "pass" ? "Passed" : r.verdict === "fail" ? "Failed" : "Not marked"}
                      </span>
                      <span className="text-[14px] font-medium">{r.question}</span>
                      <span className="text-[12px] faint">{r.seconds}s</span>
                    </div>
                    <p className="mt-1 text-[13px] muted">Expected: {r.expect}</p>
                    {r.reason ? <p className="mt-1 text-[13px]">{r.reason}</p> : null}
                    <details className="mt-1">
                      <summary className="cursor-pointer text-[13px] font-medium" style={{ color: "var(--brand-deep)" }}>
                        Its answer
                      </summary>
                      <p className="mt-1 whitespace-pre-wrap break-words text-[13.5px]">{r.answer || "(no answer)"}</p>
                    </details>
                  </li>
                ))}
              </ul>
              {failed.length ? (
                <button type="button" className="btn btn-primary mt-4" onClick={() => changeIt("", failed)}>
                  Ask the AI to fix what failed
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function TestsEditor({ tests, onChange }: { tests: CodeTest[]; onChange: (t: CodeTest[]) => void }) {
  const edit = (i: number, p: Partial<CodeTest>) => onChange(tests.map((t, j) => (j === i ? { ...t, ...p } : t)));
  return (
    <div className="card p-6">
      <h3 className="text-[17px] font-semibold">Tests</h3>
      <p className="mt-1 text-[13px] muted">Asked once it is deployed. Written by the AI; change them freely, it does not need a new approval.</p>
      <div className="mt-3 space-y-3">
        {tests.map((t, i) => (
          <div key={i} className="grid items-start gap-2 rounded-xl border p-3 sm:grid-cols-[1fr_1fr_auto]" style={{ borderColor: "var(--line)" }}>
            <label className="block">
              <span className="text-[12px] font-medium muted">Question</span>
              <textarea className="field mt-1" rows={2} value={t.question} maxLength={1000} onChange={(e) => edit(i, { question: e.target.value })} />
            </label>
            <label className="block">
              <span className="text-[12px] font-medium muted">A good answer</span>
              <textarea className="field mt-1" rows={2} value={t.expect} maxLength={600} onChange={(e) => edit(i, { expect: e.target.value })} />
            </label>
            <button type="button" className="btn btn-quiet justify-self-end !px-2 sm:mt-6" aria-label="Remove this test" onClick={() => onChange(tests.filter((_, j) => j !== i))}>
              <TrashIcon size={15} />
            </button>
          </div>
        ))}
      </div>
      <button type="button" className="btn btn-quiet mt-3" disabled={tests.length >= 10} onClick={() => onChange([...tests, { question: "", expect: "" }])}>
        <PlusIcon size={15} /> Add a test
      </button>
    </div>
  );
}
