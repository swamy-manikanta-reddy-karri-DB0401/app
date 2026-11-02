"use client";

import { useEffect, useMemo, useState } from "react";
import { api, CustomOptions, CustomSpec } from "@/lib/api";
import { Access, AccessStep, Section, VolumeField, WizardFrame } from "../BuilderParts";
import { Select, Spinner } from "../bits";
import { CloseIcon, PlusIcon } from "../icons";
import { Abilities, Rules } from "./parts";

/** Build or change a custom agent: one the portal runs. Saving makes a draft; nobody but the
 *  builders can use it until it is tested and approved (that happens on its page). */

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
};

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
  const steps = [
    { key: "name", title: "Name it" },
    { key: "instructions", title: "Instructions and rules" },
    { key: "model", title: "AI model" },
    { key: "abilities", title: "What it can use" },
    { key: "tests", title: "Test questions" },
    { key: "people", title: "Who will use it" },
    { key: "review", title: "Review" },
  ];
  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(editing ? steps.length - 1 : 0);
  const [spec, setSpec] = useState<CustomSpec>(() => ({ ...EMPTY, ...(initial || {}) }));
  const [opts, setOpts] = useState<CustomOptions | null>(null);
  const [optsErr, setOptsErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [rule, setRule] = useState("");
  const [vol, setVol] = useState("");
  const [sub, setSub] = useState("");

  useEffect(() => {
    api
      .customOptions()
      .then((o) => {
        setOpts(o);
        // The best model this admin may use, if none is chosen yet (Claude first: it is what was tested).
        setSpec((s) => (s.model ? s : { ...s, model: (o.models.find((m) => m.recommended && m.tools !== false) || o.models[0])?.name || "" }));
      })
      .catch((e) => setOptsErr(e.message));
  }, []);

  const set = (patch: Partial<CustomSpec>) => setSpec((s) => ({ ...s, ...patch }));
  const usesAbilities = spec.documents.length + spec.tools.length + spec.data.length + spec.assistants.length > 0;
  const model = opts?.models.find((m) => m.name === spec.model);
  const goodTests = spec.tests.filter((t) => t.question.trim()).length;
  const key = steps[step].key;

  const problem = useMemo(() => {
    if (key === "name") return !spec.name.trim() ? "Give it a name." : !spec.description.trim() ? "Say what it helps with." : "";
    if (key === "instructions") return spec.instructions.trim().length < 20 ? "Write its instructions (a few sentences)." : "";
    if (key === "model") return !spec.model ? "Choose a model." : "";
    if (key === "abilities" && usesAbilities && model?.tools === false) return `${model.label} cannot use tools. Choose another model.`;
    if (key === "tests") return goodTests < 2 ? "Write at least two test questions." : "";
    return "";
  }, [key, spec, usesAbilities, model, goodTests]);

  function go(i: number) {
    setStep(i);
    setReached((r) => Math.max(r, i));
  }

  function addFolder() {
    if (vol.split(".").length !== 3) return;
    const path = "/Volumes/" + vol.split(".").join("/") + (sub.trim() ? "/" + sub.trim().replace(/^\/+|\/+$/g, "") : "");
    if (!spec.documents.includes(path)) set({ documents: [...spec.documents, path] });
    setVol("");
    setSub("");
  }

  async function finish() {
    setBusy(true);
    setErr("");
    const body = { ...spec, tests: spec.tests.filter((t) => t.question.trim()) };
    try {
      const out = id ? await api.customSave(id, body) : await api.customCreate(body);
      onSaved(out.id, out.warnings || []);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const toggle = <T,>(list: T[], item: T, same: (a: T, b: T) => boolean) =>
    list.some((x) => same(x, item)) ? list.filter((x) => !same(x, item)) : [...list, item];

  return (
    <WizardFrame
      title={editing ? `Change “${initial?.name || "custom agent"}”` : "Build a custom agent"}
      steps={steps}
      step={step}
      reached={reached}
      editing={editing}
      onGo={go}
      err={err}
      problem={problem}
      busy={busy}
      finishLabel="Save as a draft"
      finishDisabled={!!problem || !spec.name.trim() || goodTests < 2}
      onFinish={finish}
      onCancel={onCancel}
    >
      {key === "name" ? (
        <div className="space-y-5">
          <div>
            <h3 className="text-lg font-semibold">What is it called, and what is it for?</h3>
            <p className="mt-1 text-[15px] muted">
              A custom agent runs in this portal. You choose its AI model and what it can use. It is tested and approved before anyone
              else can use it.
            </p>
          </div>
          <label className="block">
            <span className="label">Name</span>
            <input className="field mt-1" value={spec.name} onChange={(e) => set({ name: e.target.value })} maxLength={80} placeholder="Rate card helper" />
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

      {key === "instructions" ? (
        <div className="space-y-6">
          <label className="block">
            <span className="label">Instructions</span>
            <span className="help !mt-0">What it does, step by step, how it answers and in what tone. Write it as you would brief a new colleague.</span>
            <textarea
              className="field mt-2 min-h-[220px]"
              value={spec.instructions}
              onChange={(e) => set({ instructions: e.target.value })}
              maxLength={12000}
              placeholder={"You help our media planners price campaigns.\n1. Ask for the budget and dates if they are missing.\n2. Look up prices in the rate card.\nKeep answers short and say which file a price came from."}
            />
          </label>
          <div>
            <p className="label">Rules it must never break</p>
            <p className="help !mt-0">Each rule is also tested: a question tries to make it break the rule, and it must decline.</p>
            <ul className="mt-2 space-y-1.5">
              {spec.rules.map((r) => (
                <li key={r} className="flex items-start gap-2 rounded-lg px-3 py-2" style={{ background: "var(--canvas)" }}>
                  <span className="min-w-0 flex-1 break-words text-[14px]">{r}</span>
                  <button type="button" className="icon-btn !h-7 !w-7" aria-label={`Remove rule ${r}`} onClick={() => set({ rules: spec.rules.filter((x) => x !== r) })}>
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
                placeholder="Never share prices with anyone outside the company."
                onKeyDown={(e) => {
                  if (e.key === "Enter" && rule.trim()) {
                    e.preventDefault();
                    set({ rules: Array.from(new Set([...spec.rules, rule.trim()])) });
                    setRule("");
                  }
                }}
              />
              <button
                type="button"
                className="btn btn-quiet"
                disabled={!rule.trim() || spec.rules.length >= 20}
                onClick={() => {
                  set({ rules: Array.from(new Set([...spec.rules, rule.trim()])) });
                  setRule("");
                }}
              >
                <PlusIcon size={15} /> Add
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {key === "model" ? (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold">Which AI model should it use?</h3>
          {optsErr ? <p className="error">{optsErr}</p> : null}
          {!opts && !optsErr ? <Spinner label="Loading the models you can use…" /> : null}
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
                  Only models you may use are listed. People who use the agent need permission to use this model too. Claude models are
                  recommended: they are the ones this was tested with.
                </span>
              </label>
            ) : (
              <p className="notice">You cannot use any AI model in this workspace. Ask an admin to give you Can Query on one.</p>
            )
          ) : null}
        </div>
      ) : null}

      {key === "abilities" ? (
        <div className="space-y-8">
          <div>
            <h3 className="text-lg font-semibold">What can it use to answer?</h3>
            <p className="mt-1 text-[15px] muted">All optional. Choose only what it needs.</p>
          </div>

          <section>
            <p className="label">Documents</p>
            <p className="help !mt-0">
              Files in these folders (PDF, Word, PowerPoint, Excel, CSV, text) are read when you save. Each person must be allowed to read
              the folder for it to use them for them.
            </p>
            <ul className="mt-2 space-y-1.5">
              {spec.documents.map((d) => (
                <li key={d} className="flex items-center gap-2 rounded-lg px-3 py-2 font-mono text-[13px]" style={{ background: "var(--canvas)" }}>
                  <span className="min-w-0 flex-1 break-all">{d}</span>
                  <button type="button" className="icon-btn !h-7 !w-7" aria-label={`Remove ${d}`} onClick={() => set({ documents: spec.documents.filter((x) => x !== d) })}>
                    <CloseIcon size={13} />
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-3 rounded-xl p-4" style={{ border: "1px solid var(--line)" }}>
              <VolumeField label="Add a folder" hint="" value={vol} suffix={sub.trim() ? "/" + sub.trim() : ""} onChange={setVol} />
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <label className="min-w-[200px] flex-1">
                  <span className="text-[13px] font-medium muted">Sub-folder (optional)</span>
                  <input className="field mt-1" value={sub} onChange={(e) => setSub(e.target.value)} placeholder="rate-cards" />
                </label>
                <button type="button" className="btn btn-quiet" disabled={vol.split(".").length !== 3} onClick={addFolder}>
                  <PlusIcon size={15} /> Add this folder
                </button>
              </div>
            </div>
          </section>

          <Pick
            title="Tools"
            help="Ready-made tools it can call. Tools that change something wait for the person to approve them in the chat."
            note={opts?.notes.tools}
            loading={!opts}
            items={(opts?.tools || []).map((t) => ({
              id: t.app,
              label: t.name,
              detail: t.running ? t.description : `Not running yet${t.note ? ` (${t.note})` : ""}. Install it from the Build page first.`,
              disabled: !t.running,
              on: spec.tools.some((x) => x.app === t.app),
              toggle: () => set({ tools: toggle(spec.tools, { app: t.app, name: t.name }, (a, b) => a.app === b.app) }),
            }))}
          />
          {spec.tools.length ? (
            <label className="flex items-start gap-3 text-[14px]">
              <input type="checkbox" className="mt-1" checked={spec.ask_before_changes} onChange={(e) => set({ ask_before_changes: e.target.checked })} />
              <span>
                <b>Ask the person before a tool changes something</b>
                <span className="block muted">Recommended. Looking things up never needs approval.</span>
              </span>
            </label>
          ) : null}

          <Pick
            title="Data"
            help="Data assistants (Genie spaces) it can ask for figures. People also need permission to read their tables."
            note={opts?.notes.data}
            loading={!opts}
            items={(opts?.data || []).map((d) => ({
              id: d.space_id,
              label: d.title || "Untitled",
              detail: d.description,
              on: spec.data.some((x) => x.space_id === d.space_id),
              toggle: () => set({ data: toggle(spec.data, { space_id: d.space_id, title: d.title }, (a, b) => a.space_id === b.space_id) }),
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
                toggle: () => set({ assistants: toggle(spec.assistants, { key: a.key, name: a.name }, (x, y) => x.key === y.key) }),
              }))}
          />
        </div>
      ) : null}

      {key === "tests" ? (
        <div className="space-y-4">
          <div>
            <h3 className="text-lg font-semibold">How will you know it works?</h3>
            <p className="mt-1 text-[15px] muted">
              Write questions people will really ask, and what a good answer says. They are asked for real before it can be approved. At
              least two.
            </p>
          </div>
          {spec.tests.map((t, i) => (
            <div key={i} className="grid gap-2 rounded-xl p-4 md:grid-cols-2" style={{ border: "1px solid var(--line)" }}>
              <label className="block">
                <span className="text-[13px] font-medium muted">Question {i + 1}</span>
                <textarea
                  className="field mt-1 min-h-[70px]"
                  value={t.question}
                  maxLength={600}
                  onChange={(e) => set({ tests: spec.tests.map((x, j) => (j === i ? { ...x, question: e.target.value } : x)) })}
                />
              </label>
              <label className="block">
                <span className="text-[13px] font-medium muted">A good answer…</span>
                <textarea
                  className="field mt-1 min-h-[70px]"
                  value={t.expect}
                  maxLength={600}
                  placeholder="gives the price from the rate card and names the file"
                  onChange={(e) => set({ tests: spec.tests.map((x, j) => (j === i ? { ...x, expect: e.target.value } : x)) })}
                />
              </label>
              {spec.tests.length > 2 ? (
                <button type="button" className="btn btn-quiet justify-self-start" onClick={() => set({ tests: spec.tests.filter((_, j) => j !== i) })}>
                  Remove
                </button>
              ) : null}
            </div>
          ))}
          <button
            type="button"
            className="btn btn-quiet"
            disabled={spec.tests.length >= 20}
            onClick={() => set({ tests: [...spec.tests, { question: "", expect: "" }] })}
          >
            <PlusIcon size={15} /> Add a question
          </button>
        </div>
      ) : null}

      {key === "people" ? (
        <AccessStep
          access={spec.audience as Access[]}
          setAccess={(a) => set({ audience: a })}
          note="They can use it once it has been tested and approved, not before. You can change this later without a new approval."
        />
      ) : null}

      {key === "review" ? (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold">Check and save</h3>
          <Section title="Name" onChange={() => go(0)}>
            <p className="text-[15px] font-medium">{spec.name}</p>
            <p className="text-[14px] muted">{spec.description}</p>
          </Section>
          <Section title="Instructions and rules" onChange={() => go(1)}>
            <p className="line-clamp-4 whitespace-pre-wrap text-[14px]">{spec.instructions}</p>
            <div className="mt-2">
              <Rules spec={spec} />
            </div>
          </Section>
          <Section title="What it uses" onChange={() => go(3)}>
            <Abilities spec={spec} />
          </Section>
          <Section title="Test questions" onChange={() => go(4)}>
            <ol className="list-decimal space-y-1 pl-5 text-[14px]">
              {spec.tests.filter((t) => t.question.trim()).map((t, i) => (
                <li key={i}>{t.question}</li>
              ))}
            </ol>
          </Section>
          <p className="help">
            Saved as a draft. Only admins can try it. Next, on its page: run the tests, then send it to the people who should approve it.
          </p>
        </div>
      ) : null}
    </WizardFrame>
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
  items: { id: string; label: string; detail?: string; disabled?: boolean; on: boolean; toggle: () => void }[];
}) {
  const [q, setQ] = useState("");
  const shown = items.filter((i) => !q || (i.label + " " + (i.detail || "")).toLowerCase().includes(q.toLowerCase()));
  return (
    <section>
      <p className="label">
        {title}
        {items.some((i) => i.on) ? <span className="ml-2 text-[13px] font-normal muted">{items.filter((i) => i.on).length} chosen</span> : null}
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
          {items.length > 8 ? <input className="field mt-2" value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${title.toLowerCase()}`} /> : null}
          <ul className="mt-2 grid max-h-80 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
            {shown.map((i) => (
              <li key={i.id}>
                <label
                  className="flex h-full cursor-pointer items-start gap-3 rounded-xl p-3"
                  style={{
                    border: "1px solid " + (i.on ? "var(--brand)" : "var(--line)"),
                    background: i.on ? "var(--brand-soft)" : "var(--surface)",
                    opacity: i.disabled ? 0.6 : 1,
                  }}
                >
                  <input type="checkbox" className="mt-1" checked={i.on} disabled={i.disabled && !i.on} onChange={i.toggle} />
                  <span className="min-w-0">
                    <span className="block break-words text-[14px] font-medium">{i.label}</span>
                    {i.detail ? <span className="line-clamp-2 block text-[12.5px] muted">{i.detail}</span> : null}
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
