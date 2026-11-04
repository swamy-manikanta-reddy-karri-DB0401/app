"use client";

import { useEffect, useRef, useState } from "react";
import { api, DesignerModels, ToolFix, ToolPlan, ToolResult } from "@/lib/api";
import { ErrorBox, Spinner } from "../bits";
import { CheckIcon } from "../icons";
import { sleep } from "./parts";

/** Try a new tool on the real files in its folders, before an assistant is built around it, and
 *  repair what breaks. It only ever calls abilities that read; anything that saves or changes
 *  something is listed as not tried. A repair is shown as exactly what changes, and nothing is
 *  installed until it has been read and approved. */

type Stage =
  | "planning"
  | "running"
  | "report"
  | "fixing"
  | "reviewing"
  | "applying"
  | "error";
const MAX_REPAIRS = 3;

const VERDICT = {
  pass: {
    label: "Worked",
    style: {
      background: "color-mix(in srgb, var(--ok) 12%, transparent)",
      color: "var(--ok)",
    },
  },
  fail: {
    label: "Failed",
    style: { background: "var(--err-bg)", color: "var(--err)" },
  },
  look: {
    label: "Worth a look",
    style: { background: "var(--warn-bg)", color: "var(--ink-dim)" },
  },
  unchecked: {
    label: "Not checked",
    style: { background: "var(--bubble)", color: "var(--ink-faint)" },
  },
} as const;

function inputWords(args: Record<string, unknown>): string {
  const bits = Object.entries(args).map(
    ([k, v]) =>
      `${k.replace(/_/g, " ")} “${String(typeof v === "string" ? v : JSON.stringify(v)).slice(0, 48)}”`,
  );
  return bits.length ? "with " + bits.join(", ") : "with nothing extra";
}

export function Diff({ lines }: { lines: string[] }) {
  return (
    <pre
      className="max-h-[360px] overflow-auto rounded-xl p-3 text-[12.5px] leading-5"
      style={{ background: "var(--bubble)" }}
      tabIndex={0}
      aria-label="What the repair changes"
    >
      {lines.map((l, i) => {
        const add = l.startsWith("+") && !l.startsWith("+++");
        const del = l.startsWith("-") && !l.startsWith("---");
        return (
          <span
            key={i}
            className="block whitespace-pre"
            style={
              add
                ? {
                    background:
                      "color-mix(in srgb, var(--ok) 14%, transparent)",
                  }
                : del
                  ? {
                      background:
                        "color-mix(in srgb, var(--err) 14%, transparent)",
                    }
                  : l.startsWith("@@")
                    ? { color: "var(--ink-faint)" }
                    : undefined
            }
          >
            {l || " "}
          </span>
        );
      })}
    </pre>
  );
}

export function ToolCheck({
  slug,
  name,
  models,
  onDone,
  onBack,
}: {
  slug: string;
  name: string;
  models: Partial<DesignerModels>;
  /** Carry on and build the assistant. */
  onDone: () => void;
  onBack: () => void;
}) {
  const [stage, setStage] = useState<Stage>("planning");
  const [plan, setPlan] = useState<ToolPlan | null>(null);
  const [results, setResults] = useState<ToolResult[]>([]);
  const [err, setErr] = useState("");
  const [fix, setFix] = useState<ToolFix | null>(null);
  const [read, setRead] = useState(false);
  const [round, setRound] = useState(0); // how many repairs have been applied
  const [runKey, setRunKey] = useState(0);
  const alive = useRef(true);
  const planRef = useRef<ToolPlan | null>(null);
  // The workshop: the admin adds their own tests and asks for changes in their own words.
  const [touched, setTouched] = useState(false);
  const [asks, setAsks] = useState("");
  const [change, setChange] = useState("");
  const [wsBusy, setWsBusy] = useState("");
  const [wsErr, setWsErr] = useState("");
  // Developer mode: edit the code by hand. The edit goes through the same review as a repair.
  const [dev, setDev] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  useEffect(() => {
    api
      .developerStatus()
      .then((d) => setDev(d.on))
      .catch(() => setDev(false));
  }, []);

  async function openEditor() {
    setWsErr("");
    setWsBusy("code");
    try {
      setEditing((await api.toolCode(slug)).code);
    } catch (e: any) {
      setWsErr(e.message);
    } finally {
      setWsBusy("");
    }
  }

  async function checkEdit() {
    if (editing === null) return;
    setWsErr("");
    setWsBusy("check");
    try {
      const c = await api.checkToolCode(slug, editing);
      if (c.same) {
        setWsErr("Nothing changed yet.");
        return;
      }
      setRead(false);
      setFix({ code: editing, what: "Edited by hand.", diff: c.diff, fingerprint: c.fingerprint, problems: c.problems, no_change: false, notice: "" } as ToolFix);
      setEditing(null);
      setStage("reviewing");
    } catch (e: any) {
      setWsErr(e.message);
    } finally {
      setWsBusy("");
    }
  }

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // Plan once, then run it; after a repair the same plan is run again so the two runs can be compared.
  useEffect(() => {
    (async () => {
      setErr("");
      setResults([]);
      try {
        if (!planRef.current) {
          setStage("planning");
          const p = await api.toolTestPlan(slug, models);
          if (!alive.current) return;
          planRef.current = p;
          setPlan(p);
        }
        setStage("running");
        const out: ToolResult[] = [];
        for (const t of planRef.current!.tests) {
          const r = await api.toolTestRun(slug, t, models);
          if (!alive.current) return;
          out.push(r);
          setResults([...out]);
        }
        setStage("report");
      } catch (e: any) {
        if (!alive.current) return;
        setErr(e.message);
        setStage("error");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKey]);

  const failed = results.filter((r) => r.verdict === "fail");
  const looks = results.filter((r) => r.verdict === "look");
  const clean =
    stage === "report" && results.length > 0 && !failed.length && !looks.length;

  // Everything worked: go on to build, after a moment to read it (not while the admin is adding tests or changes).
  useEffect(() => {
    if (!clean || touched) return;
    const t = setTimeout(onDone, 2200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clean, touched]);

  // What the last repair was asked about (failures, or answers worth a look), so "Try another repair" asks the same.
  const askedRef = useRef<ToolResult[]>([]);
  const requestRef = useRef("");
  async function repair(
    what: ToolResult[] = failed.length ? failed : askedRef.current,
    request = requestRef.current,
  ) {
    askedRef.current = what;
    requestRef.current = request;
    setStage("fixing");
    setErr("");
    setRead(false);
    try {
      const f = await api.toolTestFix(slug, what, models, request);
      if (!alive.current) return;
      setFix(f);
      setStage("reviewing");
    } catch (e: any) {
      if (!alive.current) return;
      setErr(e.message);
      setStage("error");
    }
  }

  async function apply() {
    if (!fix) return;
    setStage("applying");
    setErr("");
    try {
      const a = await api.toolTestApply(
        slug,
        fix.code,
        fix.fingerprint,
        fix.what,
      );
      // The old version keeps answering until the new one is in, so wait for the new one by its time.
      for (let i = 0; i < 90 && alive.current; i++) {
        await sleep(5000);
        const r = await api.toolTestReady(slug, a.since);
        if (r.failed)
          throw new Error("The repair could not be installed: " + r.note);
        if (r.ready) {
          if (!alive.current) return;
          setRound((n) => n + 1);
          setFix(null);
          setRunKey((k) => k + 1);
          return;
        }
      }
      throw new Error(
        "The repair did not finish installing within about 7 minutes.",
      );
    } catch (e: any) {
      if (!alive.current) return;
      setErr(e.message);
      setStage("error");
    }
  }

  // One more test, described in the admin's own words, written as a real call and run at once (read-only only).
  async function addTest() {
    if (!planRef.current || asks.trim().length < 3) return;
    setWsBusy("test");
    setWsErr("");
    try {
      const p = await api.toolTestPlanOne(slug, asks.trim(), models);
      if (!alive.current) return;
      const t = p.tests[0];
      if (!t) {
        setWsErr(
          p.skipped[0]?.why ||
            "That test could not be written for this tool's abilities. Describe it another way.",
        );
        return;
      }
      planRef.current = {
        ...planRef.current,
        tests: [...planRef.current.tests, t],
      };
      setPlan(planRef.current);
      const r = await api.toolTestRun(slug, t, models);
      if (!alive.current) return;
      setResults((rs) => [...rs, r]);
      setAsks("");
    } catch (e: any) {
      if (alive.current) setWsErr(e.message);
    } finally {
      if (alive.current) setWsBusy("");
    }
  }

  function askChange() {
    if (change.trim().length < 3) return;
    setWsErr("");
    repair(failed, change.trim());
    setChange("");
  }

  const tests = plan?.tests || [];
  const rows = tests.map((t, i) => ({ t, r: results[i] }));
  const busyNow =
    stage === "planning" ||
    stage === "running" ||
    stage === "fixing" ||
    stage === "applying";

  return (
    <div className="max-w-3xl">
      <div className="card p-6">
        <h2 className="text-xl font-semibold">Trying out “{name}”</h2>
        <p className="mt-1 text-[15px] muted">
          Before your assistant is built, we try the new tool on the files that
          are really in its folders. This only reads: it never saves or changes
          anything.
          {round ? ` Repairs so far: ${round}.` : ""}
        </p>

        {stage === "planning" ? (
          <p className="mt-4">
            <Spinner label="Working out what to try, from the tool and its files…" />
          </p>
        ) : null}

        {plan && stage !== "planning" ? (
          <ul className="mt-4 divide-y" style={{ borderColor: "var(--line)" }}>
            {rows.map(({ t, r }, i) => (
              <li
                key={i}
                className="py-3"
                style={{ borderColor: "var(--line)" }}
              >
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-[15px] font-medium">
                      {t.ability.replace(/_/g, " ")}{" "}
                      <span className="font-normal muted">
                        {inputWords(t.arguments)}
                      </span>
                      {t.expect_error ? (
                        <span className="font-normal faint">
                          {" "}
                          (on purpose a bad request: it should say no clearly)
                        </span>
                      ) : null}
                    </p>
                    {r ? (
                      <p className="mt-0.5 break-words text-sm muted">
                        {r.reason}
                      </p>
                    ) : null}
                    {r?.preview ? (
                      <details className="mt-1 text-sm">
                        <summary className="cursor-pointer faint">
                          See what came back
                        </summary>
                        <p className="mt-1 whitespace-pre-wrap break-words muted">
                          {r.preview}
                        </p>
                      </details>
                    ) : null}
                  </div>
                  {r ? (
                    <span
                      className="status-pill shrink-0"
                      style={VERDICT[r.verdict].style}
                    >
                      {VERDICT[r.verdict].label}
                    </span>
                  ) : stage === "running" && i === results.length ? (
                    <span className="status-pill status-wait">
                      <Spinner label="Trying" />
                    </span>
                  ) : (
                    <span className="status-pill status-wait">Waiting</span>
                  )}
                </div>
              </li>
            ))}
            {plan.skipped.map((s) => (
              <li
                key={s.ability + s.why}
                className="py-3 text-sm"
                style={{ borderColor: "var(--line)" }}
              >
                <span className="font-medium">
                  {s.ability.replace(/_/g, " ")}
                </span>{" "}
                <span className="faint">was not tried.</span>{" "}
                <span className="muted">{s.why}</span>
              </li>
            ))}
          </ul>
        ) : null}

        {plan && stage !== "planning" && tests.length === 0 ? (
          <p className="notice mt-4">
            Nothing could be tried automatically. The reasons are above. You can
            still carry on, and the test questions after the build will try it.
          </p>
        ) : null}

        {clean ? (
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <span aria-hidden style={{ color: "var(--ok)" }}>
              <CheckIcon size={18} />
            </span>
            <p className="text-[15px]">
              <b>It worked on every try.</b>{" "}
              <span className="muted">
                That means it runs and answers, not that every answer is right.
                Building your assistant…
              </span>
            </p>
            <button
              type="button"
              className="btn btn-quiet ml-auto"
              onClick={onDone}
            >
              Continue now
            </button>
          </div>
        ) : null}

        {stage === "report" && !clean && failed.length ? (
          <div className="mt-5">
            <p className="text-[15px]">
              <b>
                {failed.length} of {results.length}
              </b>{" "}
              checks failed.{" "}
              <span className="muted">
                {round >= MAX_REPAIRS
                  ? "It is still failing after three repairs, so it needs a person to look at it."
                  : "We can try to repair it. You will see exactly what changes before anything is installed."}
              </span>
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {round < MAX_REPAIRS ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => repair()}
                >
                  Fix it
                </button>
              ) : null}
              <button type="button" className="btn btn-quiet" onClick={onDone}>
                Continue anyway
              </button>
              <button type="button" className="btn btn-quiet" onClick={onBack}>
                Stop and go back
              </button>
            </div>
          </div>
        ) : null}

        {stage === "report" && !clean && !failed.length ? (
          <div className="mt-5">
            <p className="text-[15px]">
              {tests.length ? (
                <>
                  <b>Everything ran.</b>{" "}
                  <span className="muted">
                    {looks.length
                      ? `${looks.length} answer${looks.length === 1 ? "" : "s"} could use a look (above).`
                      : "Some tries could not be checked."}
                  </span>
                </>
              ) : null}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-primary"
                onClick={onDone}
              >
                Continue and build
              </button>
              {looks.length && round < MAX_REPAIRS ? (
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => repair(looks)}
                >
                  Ask for a fix
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-quiet"
                onClick={() => setRunKey((k) => k + 1)}
              >
                Try again
              </button>
            </div>
          </div>
        ) : null}

        {stage === "fixing" ? (
          <p className="mt-5">
            <Spinner label="Writing a repair…" />
          </p>
        ) : null}

        {stage === "applying" ? (
          <div className="mt-5">
            <Spinner label="Installing the repair. This takes a minute or two…" />
            <p className="help">
              The old version keeps answering until the new one is in, so we
              wait for the new one before trying again.
            </p>
          </div>
        ) : null}

        {stage === "error" ? (
          <div className="mt-5">
            <ErrorBox>{err}</ErrorBox>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setRunKey((k) => k + 1)}
              >
                Try again
              </button>
              <button type="button" className="btn btn-quiet" onClick={onDone}>
                Skip the check and continue
              </button>
              <button type="button" className="btn btn-quiet" onClick={onBack}>
                Stop and go back
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {stage === "report" ? (
        <section
          className="card mt-4 p-6"
          aria-label="Improve the tool"
          onFocusCapture={() => setTouched(true)}
        >
          <h3 className="text-[16px] font-semibold">Make it better</h3>
          <p className="mt-1 text-[14px] muted">
            Add a test of your own, or say what should change. A change is
            written for you to read first; nothing is installed until you
            approve it, and then every test runs again.
          </p>
          {wsErr ? (
            <div className="mt-3">
              <ErrorBox>{wsErr}</ErrorBox>
            </div>
          ) : null}
          <label className="mt-4 block">
            <span className="text-[13px] font-medium muted">
              Add your own test
            </span>
            <div className="mt-1 flex flex-col gap-2 sm:flex-row">
              <input
                className="field min-w-0 flex-1"
                value={asks}
                maxLength={600}
                onChange={(e) => setAsks(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addTest()}
                placeholder="For example: read the second sheet and check the totals add up"
              />
              <button
                type="button"
                className="btn btn-quiet shrink-0"
                disabled={!!wsBusy || asks.trim().length < 3}
                onClick={addTest}
              >
                {wsBusy === "test" ? (
                  <Spinner label="Writing and running it…" />
                ) : (
                  "Add and run"
                )}
              </button>
            </div>
          </label>
          <label className="mt-4 block">
            <span className="text-[13px] font-medium muted">
              Tell it what to change
            </span>
            <textarea
              className="field mt-1 min-h-[84px]"
              value={change}
              maxLength={1500}
              onChange={(e) => setChange(e.target.value)}
              placeholder="For example: skip empty rows, and accept sheet names in any upper or lower case"
            />
          </label>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!!wsBusy || change.trim().length < 3}
              onClick={askChange}
            >
              Write the change
            </button>
            <span className="text-[12.5px] faint">
              Only changes to this tool are accepted here.
            </span>
          </div>
          {dev ? (
            <div className="mt-5 border-t pt-4" style={{ borderColor: "var(--line)" }}>
              <p className="text-[13px] font-medium muted">Developer mode</p>
              {editing === null ? (
                <button type="button" className="btn btn-quiet mt-1" disabled={!!wsBusy} onClick={openEditor}>
                  {wsBusy === "code" ? <Spinner label="Opening…" /> : "Edit the code yourself"}
                </button>
              ) : (
                <>
                  <textarea
                    className="field mt-1 min-h-[360px] font-mono text-[12.5px] leading-relaxed"
                    spellCheck={false}
                    value={editing}
                    onChange={(e) => setEditing(e.target.value)}
                    aria-label="The tool's code"
                  />
                  <p className="help">
                    The same safety checks run on your code. You then see every changed line before it is installed, and all tests run again.
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button type="button" className="btn btn-primary" disabled={!!wsBusy} onClick={checkEdit}>
                      {wsBusy === "check" ? <Spinner label="Checking…" /> : "Check my changes"}
                    </button>
                    <button type="button" className="btn btn-quiet" onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                  </div>
                </>
              )}
            </div>
          ) : null}
        </section>
      ) : null}

      {stage === "reviewing" && fix ? (
        <section className="card mt-4 p-6" aria-label="The proposed repair">
          <h3 className="text-[16px] font-semibold">
            {fix.no_change ? "The tool looks fine" : "A proposed repair"}
          </h3>
          {fix.what ? <p className="mt-1 text-[15px]">{fix.what}</p> : null}
          {fix.notice ? <p className="notice mt-3">{fix.notice}</p> : null}
          {fix.problems.length ? (
            <div className="mt-3">
              <ErrorBox>
                The repair did not pass the safety checks:{" "}
                {fix.problems.slice(0, 2).join(" ")}
              </ErrorBox>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => repair(askedRef.current)}
                >
                  Try another repair
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => setStage("report")}
                >
                  Back
                </button>
              </div>
            </div>
          ) : fix.no_change ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setRunKey((k) => k + 1)}
              >
                Run the checks again
              </button>
              <button type="button" className="btn btn-quiet" onClick={onDone}>
                Continue anyway
              </button>
            </div>
          ) : (
            <>
              <h4 className="mt-4 text-[13px] font-semibold uppercase tracking-[0.06em] faint">
                What changes
              </h4>
              <div className="mt-1">
                <Diff lines={fix.diff} />
              </div>
              <p className="help">
                Red lines are removed and green lines are added. Nothing else
                about the tool changes: not its abilities, its folders, or what
                it can reach.
              </p>
              <label className="mt-4 flex cursor-pointer items-start gap-2.5 text-[15px]">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4"
                  checked={read}
                  onChange={(e) => setRead(e.target.checked)}
                />
                <span>
                  I have read the change and I am happy for it to be installed.
                </span>
              </label>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={!read || busyNow}
                  onClick={apply}
                >
                  Install the repair and try again
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => setStage("report")}
                >
                  Not now
                </button>
              </div>
            </>
          )}
        </section>
      ) : null}
    </div>
  );
}
