"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  CustomOptions,
  CustomSpec,
  customRunbook,
  DesignerConnection,
} from "@/lib/api";
import {
  Access,
  AccessStep,
  Section,
  VolumeField,
  WizardFrame,
} from "../BuilderParts";
import { ErrorBox, Select, Spinner } from "../bits";
import { ConnectDialog } from "../designer/Connect";
import {
  CheckIcon,
  CloseIcon,
  FileIcon,
  PlusIcon,
  SparkleIcon,
} from "../icons";
import { Abilities, Rules } from "./parts";
import { DEFAULT_POLICY, PolicyEditor } from "./Policy";

/** Build or change a custom agent: one the portal runs. Saving makes a draft; nobody but the
 *  builders can use it until it is tested and approved (that happens on its page).
 *
 *  The easy path starts with the runbook the team already has: attach it, press "Write it for me",
 *  and the name, instructions, rules and test questions are drafted from it for the builder to check. */

const EMPTY: CustomSpec = {
  name: "",
  description: "",
  instructions: "",
  model: "",
  rules: [],
  documents: [],
  tools: [],
  data: [],
  assistants: [],
  tests: [
    { question: "", expect: "" },
    { question: "", expect: "" },
  ],
  audience: [],
  ask_before_changes: true,
  runbook: null,
  limits: { steps: 10, reply_tokens: 4000 },
  policy: DEFAULT_POLICY,
};

const STEPS = [
  { key: "how", title: "How it should work" },
  { key: "name", title: "Name it" },
  { key: "model", title: "AI model" },
  { key: "abilities", title: "What it can use" },
  { key: "tests", title: "Test questions" },
  { key: "limits", title: "Limits and safety" },
  { key: "people", title: "Who will use it" },
  { key: "review", title: "Review" },
];

export function CustomWizard({
  id,
  initial,
  onCancel,
  onSaved,
}: {
  id: string | null;
  initial: CustomSpec | null;
  onCancel: () => void;
  onSaved: (id: string, warnings: string[]) => void;
}) {
  const editing = !!id;
  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(editing ? STEPS.length - 1 : 0);
  const [spec, setSpec] = useState<CustomSpec>(() => ({
    ...EMPTY,
    ...(initial || {}),
  }));
  const [opts, setOpts] = useState<CustomOptions | null>(null);
  const [optsErr, setOptsErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [rule, setRule] = useState("");
  const [vol, setVol] = useState("");
  const [sub, setSub] = useState("");

  const loadOptions = () =>
    api
      .customOptions()
      .then((o) => {
        setOpts(o);
        setSpec((s) =>
          s.model
            ? s
            : {
                ...s,
                model:
                  (
                    o.models.find((m) => m.recommended && m.tools !== false) ||
                    o.models[0]
                  )?.name || "",
              },
        );
      })
      .catch((e) => setOptsErr(e.message));
  useEffect(() => {
    loadOptions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // While a tool is getting ready, look again every few seconds.
  const waiting = (opts?.tools || []).some(
    (t) => t.starting && spec.tools.some((x) => x.app === t.app),
  );
  useEffect(() => {
    if (!waiting) return;
    const t = setTimeout(loadOptions, 6000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting, opts]);

  const set = (patch: Partial<CustomSpec>) =>
    setSpec((s) => ({ ...s, ...patch }));
  const usesAbilities =
    spec.documents.length +
      spec.tools.length +
      spec.data.length +
      spec.assistants.length >
    0;
  const model = opts?.models.find((m) => m.name === spec.model);
  const goodTests = spec.tests.filter((t) => t.question.trim()).length;
  const notReady = (opts?.tools || []).filter(
    (t) => spec.tools.some((x) => x.app === t.app) && !t.running,
  );
  const key = STEPS[step].key;

  const problem = useMemo(() => {
    if (key === "name")
      return !spec.name.trim()
        ? "Give it a name."
        : !spec.description.trim()
          ? "Say what it helps with."
          : "";
    if (key === "how")
      return !spec.runbook && spec.instructions.trim().length < 20
        ? "Add your runbook, or write its instructions."
        : "";
    if (key === "model") return !spec.model ? "Choose a model." : "";
    if (key === "abilities" && usesAbilities && model?.tools === false)
      return `${model.label} cannot use tools. Choose another model.`;
    if (key === "tests")
      return goodTests < 2 ? "Write at least two test questions." : "";
    return "";
  }, [key, spec, usesAbilities, model, goodTests]);

  function go(i: number) {
    setStep(i);
    setReached((r) => Math.max(r, i));
  }

  function addFolder() {
    if (vol.split(".").length !== 3) return;
    const path =
      "/Volumes/" +
      vol.split(".").join("/") +
      (sub.trim() ? "/" + sub.trim().replace(/^\/+|\/+$/g, "") : "");
    if (!spec.documents.includes(path))
      set({ documents: [...spec.documents, path] });
    setVol("");
    setSub("");
  }

  async function finish() {
    setBusy(true);
    setErr("");
    const body = {
      ...spec,
      tests: spec.tests
        .filter((t) => t.question.trim())
        .map((t) => ({ ...t, then: (t.then || []).filter((f) => f.trim()) })),
    };
    try {
      const out = id
        ? await api.customSave(id, body)
        : await api.customCreate(body);
      onSaved(out.id, out.warnings || []);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const toggle = <T,>(list: T[], item: T, same: (a: T, b: T) => boolean) =>
    list.some((x) => same(x, item))
      ? list.filter((x) => !same(x, item))
      : [...list, item];

  return (
    <WizardFrame
      title={
        editing
          ? `Change “${initial?.name || "custom agent"}”`
          : "Build a custom agent"
      }
      steps={STEPS}
      step={step}
      reached={reached}
      editing={editing}
      onGo={go}
      err={err}
      problem={problem}
      busy={busy}
      finishLabel="Save as a draft"
      finishDisabled={
        !!problem ||
        !spec.name.trim() ||
        goodTests < 2 ||
        (!spec.runbook && spec.instructions.trim().length < 20)
      }
      onFinish={finish}
      onCancel={onCancel}
    >
      {key === "how" ? (
        <div className="space-y-6">
          <HowStep
            spec={spec}
            set={set}
            models={opts?.models || []}
            onDrafted={() => go(1)}
          />
          {/* Rules only once there is something to work from; drafting fills them in. */}
          {spec.runbook ||
          spec.instructions.trim().length >= 20 ||
          spec.rules.length ? (
            <RulesEditor spec={spec} set={set} rule={rule} setRule={setRule} />
          ) : null}
        </div>
      ) : null}

      {key === "name" ? (
        <div className="space-y-5">
          <h3 className="text-lg font-semibold">
            What is it called, and what is it for?
          </h3>
          <label className="block">
            <span className="label">Name</span>
            <input
              className="field mt-1"
              value={spec.name}
              onChange={(e) => set({ name: e.target.value })}
              maxLength={80}
              placeholder="For example: Sales helper"
            />
          </label>
          <label className="block">
            <span className="label">What it helps with</span>
            <textarea
              className="field mt-1 min-h-[90px]"
              value={spec.description}
              onChange={(e) => set({ description: e.target.value })}
              maxLength={600}
              placeholder="One or two sentences, shown on its card."
            />
          </label>
        </div>
      ) : null}

      {key === "model" ? (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold">
            Which AI model should it use?
          </h3>
          {optsErr ? <p className="error">{optsErr}</p> : null}
          {opts?.residency && opts.residency.inside_us_eu === false ? (
            <p className="notice">{opts.residency.note}</p>
          ) : null}
          {!opts && !optsErr ? (
            <Spinner label="Loading the models you can use…" />
          ) : null}
          {opts ? (
            opts.models.length ? (
              <label className="block">
                <Select
                  value={spec.model}
                  onChange={(v) => set({ model: v })}
                  options={opts.models.map((m) => ({
                    value: m.name,
                    label: m.label,
                    detail: [m.fit, m.detail].filter(Boolean).join(" · "),
                  }))}
                />
                <span className="help">
                  Only models you may use are listed. People who use the agent
                  need permission to use this model too. Claude models are
                  recommended: they are the ones this was tested with.
                </span>
              </label>
            ) : (
              <p className="notice">
                You cannot use any AI model in this workspace. Ask an admin to
                give you Can Query on one.
              </p>
            )
          ) : null}
        </div>
      ) : null}

      {key === "abilities" ? (
        <div className="space-y-8">
          <div>
            <h3 className="text-lg font-semibold">
              What can it use to answer?
            </h3>
            <p className="mt-1 text-[15px] muted">Choose only what it needs.</p>
          </div>

          <section>
            <p className="label">Documents</p>
            <p className="help !mt-0">
              Files in these folders (PDF, Word, PowerPoint, Excel, CSV, text)
              are read when you save. Each person must be allowed to read the
              folder for it to use them for them.
            </p>
            <ul className="mt-2 space-y-1.5">
              {spec.documents.map((d) => (
                <li
                  key={d}
                  className="flex items-center gap-2 rounded-lg px-3 py-2 font-mono text-[13px]"
                  style={{ background: "var(--canvas)" }}
                >
                  <span className="min-w-0 flex-1 break-all">{d}</span>
                  <button
                    type="button"
                    className="icon-btn !h-7 !w-7"
                    aria-label={`Remove ${d}`}
                    onClick={() =>
                      set({ documents: spec.documents.filter((x) => x !== d) })
                    }
                  >
                    <CloseIcon size={13} />
                  </button>
                </li>
              ))}
            </ul>
            <div
              className="mt-3 rounded-xl p-4"
              style={{ border: "1px solid var(--line)" }}
            >
              <VolumeField
                label="Add a folder"
                hint=""
                value={vol}
                suffix={sub.trim() ? "/" + sub.trim() : ""}
                onChange={setVol}
              />
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <label className="min-w-[200px] flex-1">
                  <span className="text-[13px] font-medium muted">
                    Sub-folder (optional)
                  </span>
                  <input
                    className="field mt-1"
                    value={sub}
                    onChange={(e) => setSub(e.target.value)}
                    placeholder="Leave empty to use the whole folder"
                  />
                </label>
                <button
                  type="button"
                  className="btn btn-quiet"
                  disabled={vol.split(".").length !== 3}
                  onClick={addFolder}
                >
                  <PlusIcon size={15} /> Add this folder
                </button>
              </div>
            </div>
          </section>

          <ToolsPick
            opts={opts}
            chosen={spec.tools}
            onToggle={(t) =>
              set({
                tools: toggle(
                  spec.tools,
                  { app: t.app, name: t.name },
                  (a, b) => a.app === b.app,
                ),
              })
            }
            onRefresh={loadOptions}
          />
          {spec.tools.length ? (
            <label className="flex items-start gap-3 text-[14px]">
              <input
                type="checkbox"
                className="mt-1"
                checked={spec.ask_before_changes}
                onChange={(e) => set({ ask_before_changes: e.target.checked })}
              />
              <span>
                <b>Ask the person before a tool changes something</b>
                <span className="block muted">
                  Recommended: saving a file or sending something waits for a
                  yes in the chat. Looking things up never does.
                </span>
              </span>
            </label>
          ) : null}
          {notReady.length ? (
            <p className="notice">
              {notReady.map((t) => t.name).join(", ")}{" "}
              {notReady.length === 1 ? "is" : "are"} not running yet. You can
              save now; the agent works without
              {notReady.length === 1 ? " it" : " them"} until{" "}
              {notReady.length === 1 ? "it is" : "they are"} ready, and the
              tests will show it.
            </p>
          ) : null}

          <Pick
            title="Data"
            help="Data assistants (Genie spaces) it can ask for figures. People also need permission to read their tables."
            note={opts?.notes.data}
            loading={!opts}
            items={(opts?.data || [])
              .filter((d) => !/\(auto-created\)/i.test(d.title || ""))
              .map((d) => ({
                id: d.space_id,
                label: d.title || "Untitled",
                detail: d.description,
                on: spec.data.some((x) => x.space_id === d.space_id),
                toggle: () =>
                  set({
                    data: toggle(
                      spec.data,
                      { space_id: d.space_id, title: d.title },
                      (a, b) => a.space_id === b.space_id,
                    ),
                  }),
              }))}
          />

          <Pick
            title="Other assistants"
            help="Assistants it can hand a question to. They answer as the person, so only ones the person can use are asked."
            loading={!opts}
            items={(opts?.assistants || [])
              .filter((a) => !id || !a.key.startsWith("ca:" + id))
              .map((a) => ({
                id: a.key,
                label: a.name,
                detail: a.kind,
                on: spec.assistants.some((x) => x.key === a.key),
                toggle: () =>
                  set({
                    assistants: toggle(
                      spec.assistants,
                      { key: a.key, name: a.name },
                      (x, y) => x.key === y.key,
                    ),
                  }),
              }))}
          />

          <section>
            <p className="label">
              Memory{" "}
              <span
                className="ml-1 rounded-full px-2 py-0.5 text-[11px] font-semibold"
                style={{
                  background: "var(--brand-soft)",
                  color: "var(--brand-deep)",
                }}
              >
                Beta
              </span>
            </p>
            <label className="mt-1 flex cursor-pointer items-start gap-2.5">
              <input
                type="checkbox"
                className="mt-1 h-4 w-4"
                checked={!!spec.memory?.on}
                onChange={(e) => set({ memory: { on: e.target.checked } })}
              />
              <span>
                <span className="text-[14px] font-medium">
                  Remember each person between chats
                </span>
                <span className="block text-[13px] muted">
                  It keeps short facts people tell it (their currency, team,
                  usual markets) so it does not ask again. Each person sees only
                  their own notes and can clear them. Keys are never kept, and
                  personal data is hidden if its safety policy hides it. Tests
                  never use memory.
                </span>
              </span>
            </label>
          </section>
        </div>
      ) : null}

      {key === "tests" ? (
        <div className="space-y-4">
          <div>
            <h3 className="text-lg font-semibold">
              How will you know it works?
            </h3>
            <p className="mt-1 text-[15px] muted">
              Test conversations, and what a good answer does. Each one is run
              for real before it can be approved. Let AI write them, then check
              them: you decide what a good answer is.
            </p>
          </div>
          <SuggestTests spec={spec} set={set} models={opts?.models || []} />
          {spec.tests.map((t, i) => (
            <div
              key={i}
              className="grid gap-2 rounded-xl p-4 md:grid-cols-2"
              style={{ border: "1px solid var(--line)" }}
            >
              <div>
                <label className="block">
                  <span className="text-[13px] font-medium muted">
                    Test {i + 1}: the person says
                  </span>
                  <textarea
                    className="field mt-1 min-h-[70px]"
                    value={t.question}
                    maxLength={600}
                    onChange={(e) =>
                      set({
                        tests: spec.tests.map((x, j) =>
                          j === i ? { ...x, question: e.target.value } : x,
                        ),
                      })
                    }
                  />
                </label>
                <label className="mt-2 flex cursor-pointer items-start gap-2 text-[13.5px]">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4"
                    checked={!!t.persona}
                    onChange={(e) =>
                      set({
                        tests: spec.tests.map((x, j) =>
                          j === i
                            ? e.target.checked
                              ? {
                                  ...x,
                                  then: [],
                                  persona: { who: "", goal: "", knows: "" },
                                }
                              : { ...x, persona: undefined }
                            : x,
                        ),
                      })
                    }
                  />
                  <span>
                    <span className="font-medium">
                      Let an AI play this person
                    </span>
                    <span className="block text-[12.5px] muted">
                      It answers the agent&apos;s questions from what they know,
                      until their goal is met.
                    </span>
                  </span>
                </label>
                {t.persona ? (
                  <div
                    className="mt-2 space-y-2 rounded-lg p-3"
                    style={{ background: "var(--bubble)" }}
                  >
                    {(
                      [
                        [
                          "who",
                          "Who they are",
                          "e.g. a media planner at an agency",
                        ],
                        [
                          "goal",
                          "What they want by the end",
                          "e.g. a plan for 50,000 USD in May",
                        ],
                        [
                          "knows",
                          "What they know and will give when asked",
                          "e.g. Budget 50,000 USD. Dates 1-31 May. No podcast.",
                        ],
                      ] as const
                    ).map(([k, label, ph]) => (
                      <label key={k} className="block">
                        <span className="text-[12.5px] font-medium muted">
                          {label}
                        </span>
                        <textarea
                          className="field mt-1 min-h-[44px]"
                          value={t.persona?.[k] || ""}
                          maxLength={k === "knows" ? 1200 : 400}
                          placeholder={ph}
                          onChange={(e) =>
                            set({
                              tests: spec.tests.map((x, j) =>
                                j === i
                                  ? {
                                      ...x,
                                      persona: {
                                        ...(x.persona || {
                                          who: "",
                                          goal: "",
                                          knows: "",
                                        }),
                                        [k]: e.target.value,
                                      },
                                    }
                                  : x,
                              ),
                            })
                          }
                        />
                      </label>
                    ))}
                    <p className="text-[12px] faint">
                      Leave a detail out of what they know to see whether the
                      agent asks for it or makes it up.
                    </p>
                  </div>
                ) : null}
                {(t.persona ? [] : t.then || []).map((f, k) => (
                  <label key={k} className="mt-2 block">
                    <span className="flex items-center justify-between text-[13px] font-medium muted">
                      Then, after its answer, they say
                      <button
                        type="button"
                        className="text-[12.5px] underline"
                        onClick={() =>
                          set({
                            tests: spec.tests.map((x, j) =>
                              j === i
                                ? {
                                    ...x,
                                    then: (x.then || []).filter(
                                      (_, n) => n !== k,
                                    ),
                                  }
                                : x,
                            ),
                          })
                        }
                      >
                        Remove
                      </button>
                    </span>
                    <textarea
                      className="field mt-1 min-h-[56px]"
                      value={f}
                      maxLength={600}
                      onChange={(e) =>
                        set({
                          tests: spec.tests.map((x, j) =>
                            j === i
                              ? {
                                  ...x,
                                  then: (x.then || []).map((y, n) =>
                                    n === k ? e.target.value : y,
                                  ),
                                }
                              : x,
                          ),
                        })
                      }
                    />
                  </label>
                ))}
                {!t.persona && (t.then || []).length < 6 ? (
                  <button
                    type="button"
                    className="mt-2 text-[13px] font-medium"
                    style={{ color: "var(--brand-deep)" }}
                    onClick={() =>
                      set({
                        tests: spec.tests.map((x, j) =>
                          j === i ? { ...x, then: [...(x.then || []), ""] } : x,
                        ),
                      })
                    }
                  >
                    + Add what they say next
                  </button>
                ) : null}
              </div>
              <label className="block">
                <span className="text-[13px] font-medium muted">
                  A good {(t.then || []).length ? "last " : ""}answer…
                </span>
                <textarea
                  className="field mt-1 min-h-[70px]"
                  value={t.expect}
                  maxLength={600}
                  placeholder="for example: answers from the right document and says which one"
                  onChange={(e) =>
                    set({
                      tests: spec.tests.map((x, j) =>
                        j === i ? { ...x, expect: e.target.value } : x,
                      ),
                    })
                  }
                />
              </label>
              {spec.tests.length > 2 ? (
                <button
                  type="button"
                  className="btn btn-quiet justify-self-start"
                  onClick={() =>
                    set({ tests: spec.tests.filter((_, j) => j !== i) })
                  }
                >
                  Remove
                </button>
              ) : null}
            </div>
          ))}
          <button
            type="button"
            className="btn btn-quiet"
            disabled={spec.tests.length >= 20}
            onClick={() =>
              set({ tests: [...spec.tests, { question: "", expect: "" }] })
            }
          >
            <PlusIcon size={15} /> Add a question
          </button>
          <RubricEditor
            rubric={spec.rubric || { criteria: [], threshold: 0.7 }}
            onChange={(rubric) => set({ rubric })}
          />
        </div>
      ) : null}

      {key === "limits" ? (
        <div className="space-y-8">
          <LimitsStep spec={spec} set={set} price={model?.detail || ""} />
          <section>
            <h3 className="text-lg font-semibold">Safety policy</h3>
            <p className="mt-1 text-[15px] muted">
              Checked by the portal on every message and answer. Documents and
              tool results are always treated as data it never takes orders
              from, and every full test also runs a fixed set of safety checks.
            </p>
            <div className="mt-4">
              <PolicyEditor
                policy={spec.policy || DEFAULT_POLICY}
                onChange={(policy) => set({ policy })}
                models={opts?.models || []}
              />
            </div>
          </section>
        </div>
      ) : null}

      {key === "people" ? (
        <AccessStep
          access={spec.audience as Access[]}
          setAccess={(a) => set({ audience: a })}
          note="They can use it once it has been tested and approved, not before. You can change who uses it later; it won't need to be tested or approved again."
        />
      ) : null}

      {key === "review" ? (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold">Check and save</h3>
          <Section title="Name" onChange={() => go(1)}>
            <p className="text-[15px] font-medium">{spec.name}</p>
            <p className="text-[14px] muted">{spec.description}</p>
          </Section>
          <Section title="How it works" onChange={() => go(0)}>
            {spec.runbook ? (
              <p className="text-[14px]">
                <b>Runbook:</b> {spec.runbook.name} (
                {spec.runbook.text.length.toLocaleString()} characters)
              </p>
            ) : null}
            <p className="line-clamp-4 whitespace-pre-wrap text-[14px]">
              {spec.instructions}
            </p>
            <div className="mt-2">
              <Rules spec={spec} />
            </div>
          </Section>
          <Section title="What it uses" onChange={() => go(3)}>
            <Abilities spec={spec} />
          </Section>
          <Section title="Test questions" onChange={() => go(4)}>
            <ol className="list-decimal space-y-1 pl-5 text-[14px]">
              {spec.tests
                .filter((t) => t.question.trim())
                .map((t, i) => (
                  <li key={i}>{t.question}</li>
                ))}
            </ol>
          </Section>
          <p className="help">
            Saved as a draft. Only admins can try it. Next, on its page: run the
            tests, then send it to the people who should approve it.
          </p>
        </div>
      ) : null}
    </WizardFrame>
  );
}

/** Step 1: how it should work. One obvious action: drop the runbook the team already has (it is the
 *  procedure; instructions are then only optional notes). No runbook? One quiet link to write
 *  instructions instead. Never both as two truths. */
function HowStep({
  spec,
  set,
  models,
  onDrafted,
}: {
  spec: CustomSpec;
  set: (p: Partial<CustomSpec>) => void;
  models: CustomOptions["models"];
  onDrafted: () => void;
}) {
  const [write, setWrite] = useState(
    !spec.runbook && spec.instructions.trim().length > 0,
  );
  if (write) {
    return (
      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-semibold">How should it work?</h3>
          <p className="mt-1 text-[15px] muted">
            Say what it does, step by step, as you would brief a new colleague.
          </p>
        </div>
        <textarea
          className="field min-h-[240px]"
          value={spec.instructions}
          onChange={(e) => set({ instructions: e.target.value })}
          maxLength={12000}
          autoFocus
          placeholder={
            "You help our team with ...\n1. Ask for anything that is missing.\n2. Look it up in the documents or with the tools.\nKeep answers short and say where each answer came from."
          }
        />
        <button
          type="button"
          className="text-[13.5px] font-medium"
          style={{ color: "var(--brand-deep)" }}
          onClick={() => setWrite(false)}
        >
          ← Use a runbook instead
        </button>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold">How should it work?</h3>
        <p className="mt-1 text-[15px] muted">
          Add your runbook: the procedure your team already follows. We write
          the rest from it.
        </p>
      </div>
      <RunbookPanel
        spec={spec}
        set={set}
        models={models}
        onDrafted={onDrafted}
      />
      {!spec.runbook ? (
        <button
          type="button"
          className="text-[13.5px] font-medium"
          style={{ color: "var(--brand-deep)" }}
          onClick={() => setWrite(true)}
        >
          No runbook? Write instructions instead →
        </button>
      ) : null}
    </div>
  );
}

/** AI writes the test conversations (the builder picks the model); they arrive as ordinary, editable
 *  tests. Ones already there are kept, and the same question is not added twice. */
function SuggestTests({
  spec,
  set,
  models,
}: {
  spec: CustomSpec;
  set: (p: Partial<CustomSpec>) => void;
  models: CustomOptions["models"];
}) {
  const usable = models.filter((m) => m.tools !== false);
  const [writer, setWriter] = useState("");
  const pick =
    writer ||
    spec.model ||
    (usable.find((m) => m.recommended) || usable[0])?.name ||
    "";
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [added, setAdded] = useState(0);
  const filled = spec.tests.filter((t) => t.question.trim());
  const ready = !!spec.runbook || spec.instructions.trim().length >= 20;

  async function write() {
    setBusy(true);
    setErr("");
    setAdded(0);
    try {
      const r = await api.customSuggestTests({ ...spec, tests: filled }, pick);
      const next = [...filled, ...r.tests].slice(0, 20);
      set({
        tests:
          next.length >= 2
            ? next
            : [
                ...next,
                ...Array(2 - next.length).fill({ question: "", expect: "" }),
              ],
      });
      setAdded(r.tests.length);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="rounded-2xl p-5"
      style={{ background: "var(--brand-soft)" }}
    >
      <div className="flex flex-wrap items-end justify-between gap-4">
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-[16px] font-semibold">
            <SparkleIcon size={17} />{" "}
            {filled.length
              ? "Write more test conversations"
              : "Let AI write the test conversations"}
          </span>
          <span className="mt-0.5 block text-[13.5px] muted">
            Covers each step, missing information, bad data, a request it should
            turn down and edge cases, from{" "}
            {spec.runbook ? "your runbook" : "its instructions"} and what it can
            use.
          </span>
          <label className="mt-3 block max-w-sm">
            <span className="text-[12.5px] font-medium muted">Written by</span>
            <Select
              className="mt-1"
              value={pick}
              onChange={setWriter}
              loading={!models.length}
              options={usable.map((m) => ({
                value: m.name,
                label: m.label,
                detail: [m.fit, m.detail].filter(Boolean).join(" · "),
              }))}
            />
          </label>
        </span>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || !pick || !ready}
          onClick={write}
        >
          {busy
            ? "Writing… (about a minute)"
            : filled.length
              ? "Write more"
              : "Write them for me"}
        </button>
      </div>
      {!ready ? (
        <p className="mt-2 text-[13px] muted">
          Add the runbook or instructions in step 1 first.
        </p>
      ) : null}
      {added ? (
        <p className="mt-2 text-[13px]" role="status">
          Added {added} test conversation{added === 1 ? "" : "s"} below. Read
          each one and change what a good answer is if you disagree.
        </p>
      ) : null}
      {err ? (
        <p className="mt-2 text-[13px]" style={{ color: "var(--err)" }}>
          {err}
        </p>
      ) : null}
    </div>
  );
}

/** Rules, folded away until there is something to work from; "Write the rest for me" fills them in. */
function RulesEditor({
  spec,
  set,
  rule,
  setRule,
}: {
  spec: CustomSpec;
  set: (p: Partial<CustomSpec>) => void;
  rule: string;
  setRule: (v: string) => void;
}) {
  const add = () => {
    if (!rule.trim()) return;
    set({ rules: Array.from(new Set([...spec.rules, rule.trim()])) });
    setRule("");
  };
  return (
    <details
      className="rounded-xl"
      style={{ border: "1px solid var(--line)" }}
      open={spec.rules.length > 0}
    >
      <summary className="cursor-pointer select-none px-4 py-3 text-[14.5px] font-semibold">
        Rules it must never break{" "}
        <span className="font-normal muted">
          {spec.rules.length ? `· ${spec.rules.length}` : "· optional"}
        </span>
      </summary>
      <div
        className="border-t px-4 pb-4 pt-3"
        style={{ borderColor: "var(--line)" }}
      >
        <p className="text-[13px] muted">
          A full test checks each one: a message tries to make it break the
          rule, and it must keep to it.
        </p>
        <ul className="mt-2 space-y-1.5">
          {spec.rules.map((r) => (
            <li
              key={r}
              className="flex items-start gap-2 rounded-lg px-3 py-2"
              style={{ background: "var(--canvas)" }}
            >
              <span className="min-w-0 flex-1 break-words text-[14px]">
                {r}
              </span>
              <button
                type="button"
                className="icon-btn !h-7 !w-7"
                aria-label={`Remove rule ${r}`}
                onClick={() =>
                  set({ rules: spec.rules.filter((x) => x !== r) })
                }
              >
                <CloseIcon size={13} />
              </button>
            </li>
          ))}
        </ul>
        <div className="mt-2 flex gap-2">
          <input
            className="field flex-1"
            value={rule}
            onChange={(e) => setRule(e.target.value)}
            maxLength={300}
            placeholder="Add a rule, for example: never invent a price"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
          <button
            type="button"
            className="btn btn-quiet"
            disabled={!rule.trim() || spec.rules.length >= 20}
            onClick={add}
          >
            <PlusIcon size={15} /> Add
          </button>
        </div>
      </div>
    </details>
  );
}

function RunbookPanel({
  spec,
  set,
  models,
  onDrafted,
}: {
  spec: CustomSpec;
  set: (p: Partial<CustomSpec>) => void;
  models: CustomOptions["models"];
  onDrafted: () => void;
}) {
  // Which model writes the draft: the builder's choice, shown and changeable (first offer: the agent's model, else the best one).
  const usable = models.filter((m) => m.tools !== false);
  const [writer, setWriter] = useState("");
  const pick =
    writer ||
    spec.model ||
    (usable.find((m) => m.recommended) || usable[0])?.name ||
    "";
  const [reading, setReading] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [drag, setDrag] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [paste, setPaste] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const rb = spec.runbook;

  async function onFile(f?: File) {
    if (!f) return;
    setReading(true);
    setErr("");
    setNote("");
    try {
      const r = await customRunbook(f);
      set({ runbook: { name: r.name, text: r.text } });
      setNote(
        (r.removed_keys
          ? "Something that looked like a password or key was removed. "
          : "") +
          (r.searched
            ? "It is long, so the agent will search it rather than read it all every time."
            : ""),
      );
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setReading(false);
    }
  }

  async function draft() {
    if (!rb) return;
    setDrafting(true);
    setErr("");
    try {
      const d = await api.customDraft(rb.text, pick);
      const tests =
        d.tests.length >= 2
          ? d.tests
          : [
              ...d.tests,
              ...Array(2 - d.tests.length).fill({ question: "", expect: "" }),
            ];
      set({
        name: spec.name || d.name,
        description: spec.description || d.description,
        rules: Array.from(new Set([...spec.rules, ...d.rules])),
        tests,
      });
      onDrafted();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setDrafting(false);
    }
  }

  if (!rb && paste) {
    return (
      <PasteRunbook
        onDone={(name, text) => set({ runbook: { name, text } })}
        onCancel={() => setPaste(false)}
      />
    );
  }

  if (!rb) {
    return (
      <div>
        <ErrorBox>{err}</ErrorBox>
        <div
          className="flex flex-col items-center justify-center rounded-2xl px-6 py-10 text-center transition"
          style={{
            border: "2px dashed " + (drag ? "var(--brand)" : "var(--line)"),
            background: drag ? "var(--brand-soft)" : "var(--canvas)",
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            onFile(e.dataTransfer.files?.[0]);
          }}
        >
          <span className="dz-tile !h-12 !w-12 !rounded-xl" aria-hidden>
            <FileIcon size={22} />
          </span>
          <p className="mt-3 text-[16px] font-semibold">
            {reading ? "Reading your runbook…" : "Drop your runbook here"}
          </p>
          <p className="mt-1 text-[13.5px] muted">
            PDF, Word, PowerPoint or a text file
          </p>
          <div className="mt-4 flex flex-wrap items-center justify-center gap-3">
            <button
              type="button"
              className="btn btn-primary"
              disabled={reading}
              onClick={() => file.current?.click()}
            >
              Choose a file
            </button>
            <button
              type="button"
              className="text-[14px] font-medium"
              style={{ color: "var(--brand-deep)" }}
              onClick={() => setPaste(true)}
            >
              or paste the text
            </button>
          </div>
          <input
            ref={file}
            type="file"
            className="hidden"
            accept=".pdf,.docx,.pptx,.txt,.md,.html,.htm"
            onChange={(e) => onFile(e.target.files?.[0])}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ErrorBox>{err}</ErrorBox>
      <div
        className="flex flex-wrap items-center gap-3 rounded-xl px-4 py-3"
        style={{ border: "1px solid var(--line)" }}
      >
        <span className="dz-tile !h-9 !w-9 !rounded-lg" aria-hidden>
          <FileIcon size={16} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[14.5px] font-semibold">
            {rb.name}
          </span>
          <span className="block text-[12.5px] muted">
            {rb.text.length.toLocaleString()} characters · saved with the agent;
            approvers see this exact text
          </span>
        </span>
        <button
          type="button"
          className="btn btn-quiet !min-h-[34px] !px-3"
          onClick={() => set({ runbook: null })}
        >
          Remove
        </button>
      </div>
      {note ? <p className="text-[13px] muted">{note}</p> : null}

      <div
        className="flex flex-wrap items-center justify-between gap-4 rounded-2xl p-5"
        style={{ background: "var(--brand-soft)" }}
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-[16px] font-semibold">
            <SparkleIcon size={17} /> Write the rest for me
          </span>
          <span className="mt-0.5 block text-[13.5px] muted">
            Name, rules and test conversations, drafted from your runbook. You
            check them next.
          </span>
          <label className="mt-3 block max-w-sm">
            <span className="text-[12.5px] font-medium muted">Written by</span>
            <Select
              className="mt-1"
              value={pick}
              onChange={setWriter}
              loading={!models.length}
              options={usable.map((m) => ({
                value: m.name,
                label: m.label,
                detail: [m.fit, m.detail].filter(Boolean).join(" · "),
              }))}
            />
          </label>
        </span>
        <button
          type="button"
          className="btn btn-primary btn-lg"
          disabled={drafting || !pick}
          onClick={draft}
        >
          {drafting ? "Writing… (about a minute)" : "Write the rest for me"}
        </button>
      </div>

      <details>
        <summary className="cursor-pointer text-[13.5px] font-medium muted">
          Check or edit the runbook text (add anything it does not cover here)
        </summary>
        <textarea
          className="field mt-2 min-h-[220px] font-mono !text-[12.5px]"
          value={rb.text}
          onChange={(e) => set({ runbook: { ...rb, text: e.target.value } })}
        />
      </details>
    </div>
  );
}

function PasteRunbook({
  onDone,
  onCancel,
}: {
  onDone: (name: string, text: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("Runbook");
  const [text, setText] = useState("");
  return (
    <div
      className="space-y-3 rounded-2xl p-5"
      style={{ border: "1px solid var(--line)" }}
    >
      <label className="block">
        <span className="text-[13px] font-medium muted">Name</span>
        <input
          className="field mt-1"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={80}
        />
      </label>
      <label className="block">
        <span className="text-[13px] font-medium muted">The runbook</span>
        <textarea
          className="field mt-1 min-h-[220px]"
          value={text}
          onChange={(e) => setText(e.target.value)}
          autoFocus
          placeholder="Paste the steps and rules here."
        />
      </label>
      <div className="flex gap-2">
        <button
          type="button"
          className="btn btn-primary"
          disabled={text.trim().length < 40}
          onClick={() => onDone(name.trim() || "Runbook", text.trim())}
        >
          Use this text
        </button>
        <button type="button" className="btn btn-quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** Tools: tick the ones it may use; a stopped tool is started here, and a key it needs is connected here. */
function ToolsPick({
  opts,
  chosen,
  onToggle,
  onRefresh,
}: {
  opts: CustomOptions | null;
  chosen: { app: string; name: string }[];
  onToggle: (t: { app: string; name: string }) => void;
  onRefresh: () => void;
}) {
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [connect, setConnect] = useState<{
    list: DesignerConnection[];
    app: string;
  } | null>(null);
  const [q, setQ] = useState("");

  async function start(app: string) {
    setBusy(app);
    setErr("");
    try {
      const r = await api.customToolStart(app);
      if (r.needs?.length) setConnect({ list: r.needs, app });
      onRefresh();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  const tools = (opts?.tools || []).filter(
    (t) =>
      !q ||
      (t.name + " " + t.description).toLowerCase().includes(q.toLowerCase()),
  );
  return (
    <section>
      <p className="label">
        Tools
        {chosen.length ? (
          <span className="ml-2 text-[13px] font-normal muted">
            {chosen.length} chosen
          </span>
        ) : null}
      </p>
      <p className="help !mt-0">
        Ready-made tools it can call. One that is not running can be started
        from here.
      </p>
      {opts?.notes.tools ? (
        <p className="notice mt-2">{opts.notes.tools}</p>
      ) : null}
      <ErrorBox>{err}</ErrorBox>
      {!opts ? (
        <div className="mt-2">
          <Spinner label="Loading…" />
        </div>
      ) : !opts.tools.length ? (
        <p className="mt-2 text-[14px] muted">None in this workspace.</p>
      ) : (
        <>
          {opts.tools.length > 8 ? (
            <input
              className="field mt-2"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search tools"
            />
          ) : null}
          <ul className="mt-2 grid max-h-96 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
            {tools.map((t) => {
              const on = chosen.some((x) => x.app === t.app);
              return (
                <li key={t.app}>
                  <div
                    className="flex h-full items-start gap-3 rounded-xl p-3"
                    style={{
                      border:
                        "1px solid " + (on ? "var(--brand)" : "var(--line)"),
                      background: on ? "var(--brand-soft)" : "var(--surface)",
                    }}
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={on}
                      onChange={() => onToggle({ app: t.app, name: t.name })}
                      aria-label={`Use ${t.name}`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="break-words text-[14px] font-medium">
                          {t.name}
                        </span>
                        <span
                          className="status-pill"
                          style={
                            t.running
                              ? {
                                  background:
                                    "color-mix(in srgb, var(--ok) 14%, transparent)",
                                  color: "var(--ok)",
                                }
                              : {
                                  background: "var(--bubble)",
                                  color: "var(--ink-dim)",
                                }
                          }
                        >
                          {t.running ? (
                            <>
                              <CheckIcon size={11} /> Ready
                            </>
                          ) : t.starting ? (
                            "Getting ready…"
                          ) : t.needs.length ? (
                            "Needs a key"
                          ) : (
                            "Not running"
                          )}
                        </span>
                      </span>
                      {t.description ? (
                        <span className="line-clamp-2 block text-[12.5px] muted">
                          {t.description}
                        </span>
                      ) : null}
                      {!t.running && !t.starting ? (
                        <button
                          type="button"
                          className="btn btn-quiet mt-2 !min-h-[30px] !px-2.5 !text-[12.5px]"
                          disabled={busy === t.app}
                          onClick={() => start(t.app)}
                        >
                          {busy === t.app
                            ? "Starting…"
                            : t.needs.length
                              ? "Connect and start"
                              : "Start it"}
                        </button>
                      ) : null}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
      {connect && connect.list.length ? (
        <ConnectDialog
          c={connect.list[0]}
          connected={false}
          onClose={() => setConnect(null)}
          onSaved={() => {
            const rest = connect.list.slice(1);
            if (rest.length) setConnect({ list: rest, app: connect.app });
            else {
              setConnect(null);
              start(connect.app);
            }
          }}
        />
      ) : null}
    </section>
  );
}

function LimitsStep({
  spec,
  set,
  price,
}: {
  spec: CustomSpec;
  set: (p: Partial<CustomSpec>) => void;
  price: string;
}) {
  const num = (v: string) =>
    v.trim() === "" ? 0 : Math.max(0, Number(v) || 0);
  return (
    <div className="space-y-7">
      <div>
        <h3 className="text-lg font-semibold">Limits</h3>
        <p className="mt-1 text-[15px] muted">
          Sensible defaults are set. People who reach a limit get a polite
          message, never an error.
        </p>
      </div>
      <section>
        <p className="label">Each answer</p>
        <p className="help !mt-0">
          If you change these later, the agent is tested and approved again
          before people get the change.
        </p>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="text-[13px] font-medium muted">
              Most steps (each look-up or tool use is a step)
            </span>
            <input
              type="number"
              min={1}
              max={20}
              className="field mt-1"
              value={spec.limits.steps}
              onChange={(e) =>
                set({
                  limits: {
                    ...spec.limits,
                    steps: Math.min(20, Math.max(1, num(e.target.value))),
                  },
                })
              }
            />
          </label>
          <label className="block">
            <span className="text-[13px] font-medium muted">
              Longest reply (tokens; about ¾ of a word each)
            </span>
            <input
              type="number"
              min={500}
              max={8000}
              step={500}
              className="field mt-1"
              value={spec.limits.reply_tokens}
              onChange={(e) =>
                set({
                  limits: {
                    ...spec.limits,
                    reply_tokens: Math.min(
                      8000,
                      Math.max(500, num(e.target.value)),
                    ),
                  },
                })
              }
            />
          </label>
        </div>
      </section>
      <p className="help">
        The model's own price{price ? ` (${price})` : ""} decides what each
        answer costs; it is shown on the agent's page. Usage limits are part of
        the safety policy below.
      </p>
    </div>
  );
}

/** What "good" means for this agent: the checker scores every answer on its usual measures and on these, and
 *  an answer passes only if each score reaches the bar. Part of what is tested. */
function RubricEditor({
  rubric,
  onChange,
}: {
  rubric: { criteria: string[]; threshold: number };
  onChange: (r: { criteria: string[]; threshold: number }) => void;
}) {
  return (
    <section
      className="rounded-xl p-4"
      style={{ border: "1px solid var(--line)" }}
    >
      <p className="label">How answers are marked</p>
      <p className="help !mt-0">
        Each answer is scored from 0 to 1 on: answers the question, complete,
        backed by its documents and tools, nothing made up, keeps to its rules,
        and safe. Add your own measures too.
      </p>
      <div className="mt-3 space-y-2">
        {rubric.criteria.map((c, i) => (
          <div key={i} className="flex items-start gap-2">
            <input
              className="field min-w-0 flex-1"
              value={c}
              maxLength={300}
              placeholder="e.g. shows every total in USD, excluding tax"
              onChange={(e) =>
                onChange({
                  ...rubric,
                  criteria: rubric.criteria.map((x, j) =>
                    j === i ? e.target.value : x,
                  ),
                })
              }
              aria-label={`Measure ${i + 1}`}
            />
            <button
              type="button"
              className="btn btn-quiet !min-h-[40px] !px-3"
              onClick={() =>
                onChange({
                  ...rubric,
                  criteria: rubric.criteria.filter((_, j) => j !== i),
                })
              }
            >
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn btn-quiet"
          disabled={rubric.criteria.length >= 10}
          onClick={() =>
            onChange({ ...rubric, criteria: [...rubric.criteria, ""] })
          }
        >
          <PlusIcon size={15} /> Add a measure
        </button>
      </div>
      <label className="mt-4 block max-w-sm">
        <span className="text-[13px] font-medium muted">
          Every score must reach
        </span>
        <Select
          className="mt-1"
          value={String(rubric.threshold)}
          onChange={(v) => onChange({ ...rubric, threshold: Number(v) })}
          options={[
            { value: "0.5", label: "0.5", detail: "Lenient" },
            { value: "0.6", label: "0.6" },
            { value: "0.7", label: "0.7", detail: "Recommended" },
            { value: "0.8", label: "0.8" },
            { value: "0.9", label: "0.9", detail: "Strict" },
          ]}
        />
      </label>
    </section>
  );
}

function Pick({
  title,
  help,
  note,
  loading,
  items,
}: {
  title: string;
  help: string;
  note?: string;
  loading: boolean;
  items: {
    id: string;
    label: string;
    detail?: string;
    disabled?: boolean;
    on: boolean;
    toggle: () => void;
  }[];
}) {
  const [q, setQ] = useState("");
  const shown = items.filter(
    (i) =>
      !q ||
      (i.label + " " + (i.detail || ""))
        .toLowerCase()
        .includes(q.toLowerCase()),
  );
  return (
    <section>
      <p className="label">
        {title}
        {items.some((i) => i.on) ? (
          <span className="ml-2 text-[13px] font-normal muted">
            {items.filter((i) => i.on).length} chosen
          </span>
        ) : null}
      </p>
      <p className="help !mt-0">{help}</p>
      {note ? <p className="notice mt-2">{note}</p> : null}
      {loading ? (
        <div className="mt-2">
          <Spinner label="Loading…" />
        </div>
      ) : items.length === 0 ? (
        <p className="mt-2 text-[14px] muted">None in this workspace.</p>
      ) : (
        <>
          {items.length > 8 ? (
            <input
              className="field mt-2"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={`Search ${title.toLowerCase()}`}
            />
          ) : null}
          <ul className="mt-2 grid max-h-80 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
            {shown.map((i) => (
              <li key={i.id}>
                <label
                  className="flex h-full cursor-pointer items-start gap-3 rounded-xl p-3"
                  style={{
                    border:
                      "1px solid " + (i.on ? "var(--brand)" : "var(--line)"),
                    background: i.on ? "var(--brand-soft)" : "var(--surface)",
                    opacity: i.disabled ? 0.6 : 1,
                  }}
                >
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={i.on}
                    disabled={i.disabled && !i.on}
                    onChange={i.toggle}
                  />
                  <span className="min-w-0">
                    <span className="block break-words text-[14px] font-medium">
                      {i.label}
                    </span>
                    {i.detail ? (
                      <span className="line-clamp-2 block text-[12.5px] muted">
                        {i.detail}
                      </span>
                    ) : null}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
