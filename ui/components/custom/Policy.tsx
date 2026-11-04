"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  CustomOptions,
  PolicyRule,
  SafetyPolicy,
  SavedPolicy,
  UsageLimits,
} from "@/lib/api";
import { Select } from "../bits";
import { PlusIcon, TrashIcon } from "../icons";

/** The safety policy of a custom agent, in plain words: what is checked on the way in and out, the
 *  team's own rules, the model that judges them, and how much it may be used. The portal enforces it
 *  itself (safety.py), because Databricks' own filter is not available in every region. */

export const DEFAULT_POLICY: SafetyPolicy = {
  name: "Standard",
  input: {
    attacks: "warn",
    personal_data: "mask",
    secrets: "mask",
    unsafe: "off",
    max_chars: 16000,
  },
  output: { personal_data: "remove", unsafe: "off" },
  rules: [],
  checker: "",
  on_error: "allow",
  usage: {
    requests: 0,
    tokens: 0,
    cost_usd: 0,
    days: 30,
    per_person_daily: 100,
  },
};

const num = (v: string) => (v.trim() === "" ? 0 : Math.max(0, Number(v) || 0));

/** Whether this policy needs a model to judge it (unsafe content, or a rule written in plain words). */
export function needsChecker(p: SafetyPolicy) {
  return (
    p.input.unsafe === "block" ||
    p.output.unsafe === "block" ||
    p.rules.some((r) => r.kind === "rule")
  );
}

function Row({
  label,
  help,
  children,
}: {
  label: string;
  help: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_300px] sm:items-center">
      <div className="min-w-0">
        <p className="text-[14px] font-medium">{label}</p>
        <p className="text-[13px] muted">{help}</p>
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function PolicyEditor({
  policy,
  onChange,
  models,
}: {
  policy: SafetyPolicy;
  onChange: (p: SafetyPolicy) => void;
  models: CustomOptions["models"];
}) {
  const [lib, setLib] = useState<{
    presets: SavedPolicy[];
    saved: SavedPolicy[];
    can_save: boolean;
    note: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const p = policy;
  const set = (patch: Partial<SafetyPolicy>) => onChange({ ...p, ...patch });
  const inp = (patch: Partial<SafetyPolicy["input"]>) =>
    set({ input: { ...p.input, ...patch } });
  const out = (patch: Partial<SafetyPolicy["output"]>) =>
    set({ output: { ...p.output, ...patch } });
  const rule = (i: number, patch: Partial<PolicyRule>) =>
    set({ rules: p.rules.map((r, j) => (j === i ? { ...r, ...patch } : r)) });

  useEffect(() => {
    api
      .safetyPolicies()
      .then(setLib)
      .catch(() =>
        setLib({ presets: [], saved: [], can_save: false, note: "" }),
      );
  }, []);

  const starts = [...(lib?.presets || []), ...(lib?.saved || [])];
  const checkerOptions = [
    { value: "", label: "No checker", detail: "Only the fixed checks run" },
    ...models.map((m) => ({
      value: m.name,
      label: m.label,
      detail: [m.fit, m.detail].filter(Boolean).join(" · "),
    })),
  ];

  async function saveForReuse() {
    setSaving(true);
    setMsg("");
    try {
      const sv = await api.saveSafetyPolicy(
        p,
        lib?.saved.find((x) => x.name === p.name)?.id || "",
      );
      setLib((l) =>
        l ? { ...l, saved: [sv, ...l.saved.filter((x) => x.id !== sv.id)] } : l,
      );
      setMsg(`Saved as “${sv.name}”. Other agents can start from it.`);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <section>
        <p className="label">Start from</p>
        <div className="mt-1 grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            <span className="text-[13px] font-medium muted">
              Starting point
            </span>
            <Select
              className="mt-1"
              value=""
              placeholder={`Current: ${p.name}`}
              onChange={(id) => {
                const s = starts.find((x) => x.id === id);
                if (s)
                  onChange({
                    ...DEFAULT_POLICY,
                    ...s.settings,
                    name: s.name,
                    usage: s.settings.usage || p.usage,
                  });
              }}
              options={starts.map((s) => ({
                value: s.id,
                label: s.name,
                detail: s.by
                  ? `Saved by ${s.by}`
                  : s.id === "strict"
                    ? "Blocks more, checks every answer"
                    : "Sensible for most agents",
              }))}
              loading={!lib}
            />
          </div>
          <label className="block min-w-0">
            <span className="text-[13px] font-medium muted">Policy name</span>
            <input
              className="field mt-1"
              value={p.name}
              maxLength={80}
              onChange={(e) => set({ name: e.target.value })}
            />
          </label>
        </div>
        <p className="help">
          Pick a starting point, then change anything below. Give it a name to
          save it for other agents.
        </p>
      </section>

      <section>
        <p className="label">What people send</p>
        <div className="divide-y divide-[color:var(--line)]">
          <Row
            label="Attempts to change its rules"
            help="“Ignore your instructions”, “developer mode”, asking for its prompt."
          >
            <Select
              value={p.input.attacks}
              onChange={(v) =>
                inp({ attacks: v as SafetyPolicy["input"]["attacks"] })
              }
              options={[
                { value: "block", label: "Block the message" },
                {
                  value: "warn",
                  label: "Allow, remind the agent",
                  detail: "And record it",
                },
                { value: "off", label: "Don't check" },
              ]}
            />
          </Row>
          <Row
            label="Personal data"
            help="Email addresses, phone and card numbers, IBANs, SSNs, PANs."
          >
            <Select
              value={p.input.personal_data}
              onChange={(v) =>
                inp({
                  personal_data: v as SafetyPolicy["input"]["personal_data"],
                })
              }
              options={[
                { value: "block", label: "Block the message" },
                {
                  value: "mask",
                  label: "Hide it from the AI model",
                  detail: "Replaced with [email removed] and so on",
                },
                { value: "off", label: "Don't check" },
              ]}
            />
          </Row>
          <Row
            label="Passwords and keys"
            help="Never sent to the AI model either way."
          >
            <Select
              value={p.input.secrets}
              onChange={(v) =>
                inp({ secrets: v as SafetyPolicy["input"]["secrets"] })
              }
              options={[
                { value: "block", label: "Block the message" },
                { value: "mask", label: "Remove, then carry on" },
              ]}
            />
          </Row>
          <Row
            label="Unsafe requests"
            help="Crime, fraud, violence, self-harm, hate, weapons. Judged by the checker model."
          >
            <Select
              value={p.input.unsafe}
              onChange={(v) =>
                inp({ unsafe: v as SafetyPolicy["input"]["unsafe"] })
              }
              options={[
                { value: "block", label: "Block the message" },
                { value: "off", label: "Don't check" },
              ]}
            />
          </Row>
          <Row label="Longest message" help="In characters. Leave at 0 for no limit.">
            <input
              type="number"
              min={0}
              step={1000}
              className="field"
              value={p.input.max_chars}
              onChange={(e) => inp({ max_chars: num(e.target.value) })}
            />
          </Row>
        </div>
      </section>

      <section>
        <p className="label">What it answers</p>
        <div className="divide-y divide-[color:var(--line)]">
          <Row
            label="Personal data"
            help="Found in its answer, for example from a document or a tool."
          >
            <Select
              value={p.output.personal_data}
              onChange={(v) =>
                out({
                  personal_data: v as SafetyPolicy["output"]["personal_data"],
                })
              }
              options={[
                { value: "remove", label: "Remove it from the answer" },
                { value: "block", label: "Hold the whole answer back" },
                { value: "off", label: "Don't check" },
              ]}
            />
          </Row>
          <Row
            label="Unsafe content"
            help="Judged by the checker model before the person sees it."
          >
            <Select
              value={p.output.unsafe}
              onChange={(v) =>
                out({ unsafe: v as SafetyPolicy["output"]["unsafe"] })
              }
              options={[
                { value: "block", label: "Hold the answer back" },
                { value: "off", label: "Don't check" },
              ]}
            />
          </Row>
          <Row label="Passwords and keys" help="Always removed from answers.">
            <p className="text-[14px]">Always on</p>
          </Row>
        </div>
      </section>

      <section>
        <p className="label">Your own rules</p>
        <p className="help !mt-0">
          An exact phrase is matched as written (fast, free). A rule in plain
          words is judged by the checker model, for example “Never quote a
          competitor's prices”.
        </p>
        <div className="mt-2 space-y-3">
          {p.rules.map((r, i) => (
            <div
              key={i}
              className="rounded-xl border p-3"
              style={{ borderColor: "var(--line)" }}
            >
              <div className="flex items-start gap-2">
                <input
                  className="field min-w-0 flex-1"
                  value={r.text}
                  maxLength={300}
                  placeholder={
                    r.kind === "phrase"
                      ? "A word or phrase, e.g. a project code name"
                      : "A rule in plain words"
                  }
                  onChange={(e) => rule(i, { text: e.target.value })}
                  aria-label={`Rule ${i + 1}`}
                />
                <button
                  type="button"
                  className="btn btn-quiet !min-h-[40px] !px-2"
                  aria-label="Remove this rule"
                  onClick={() =>
                    set({ rules: p.rules.filter((_, j) => j !== i) })
                  }
                >
                  <TrashIcon size={15} />
                </button>
              </div>
              <div className="mt-2 grid gap-2 sm:grid-cols-3">
                <Select
                  value={r.kind}
                  ariaLabel="Kind of rule"
                  onChange={(v) =>
                    rule(i, {
                      kind: v as PolicyRule["kind"],
                      action: v === "rule" ? "block" : r.action,
                    })
                  }
                  options={[
                    { value: "phrase", label: "Exact phrase" },
                    { value: "rule", label: "Rule in plain words" },
                  ]}
                />
                <Select
                  value={r.where}
                  ariaLabel="Where it applies"
                  onChange={(v) => rule(i, { where: v as PolicyRule["where"] })}
                  options={[
                    { value: "both", label: "Messages and answers" },
                    { value: "input", label: "Messages people send" },
                    { value: "output", label: "Its answers" },
                  ]}
                />
                <Select
                  value={r.action}
                  ariaLabel="What happens"
                  onChange={(v) =>
                    rule(i, { action: v as PolicyRule["action"] })
                  }
                  options={[
                    { value: "block", label: "Block" },
                    ...(r.kind === "phrase"
                      ? [{ value: "remove", label: "Remove the phrase" }]
                      : []),
                  ]}
                />
              </div>
            </div>
          ))}
          <button
            type="button"
            className="btn btn-quiet"
            disabled={p.rules.length >= 30}
            onClick={() =>
              set({
                rules: [
                  ...p.rules,
                  { text: "", kind: "phrase", where: "both", action: "block" },
                ],
              })
            }
          >
            <PlusIcon size={15} /> Add a rule
          </button>
        </div>
      </section>

      <section>
        <p className="label">Checked by</p>
        <div className="mt-1 grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="text-[13px] font-medium muted">
              Model that judges unsafe content and plain-word rules
            </span>
            <Select
              className="mt-1"
              value={p.checker}
              onChange={(v) => set({ checker: v })}
              options={checkerOptions}
            />
          </label>
          <label className="block">
            <span className="text-[13px] font-medium muted">
              If the checker cannot answer
            </span>
            <Select
              className="mt-1"
              value={p.on_error}
              onChange={(v) => set({ on_error: v as SafetyPolicy["on_error"] })}
              options={[
                { value: "allow", label: "Let it through and record it" },
                { value: "block", label: "Hold it back" },
              ]}
            />
          </label>
        </div>
        {needsChecker(p) && !p.checker ? (
          <p className="notice mt-2">
            Choose a checker model, or set unsafe content to “Don't check” and
            use exact phrases only.
          </p>
        ) : (
          <p className="help">
            The checker reads each message and answer it is needed for, which
            adds a little time and cost to each.
          </p>
        )}
      </section>

      <section>
        <p className="label">Usage limits</p>
        <UsageFields usage={p.usage} onChange={(u) => set({ usage: u })} />
      </section>

      {lib?.can_save ? (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn btn-quiet"
            disabled={saving || !p.name.trim()}
            onClick={saveForReuse}
          >
            {saving ? "Saving…" : "Save this policy for other agents"}
          </button>
          {msg ? <span className="text-[13px] muted">{msg}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Requests, tokens and cost over a window of days, plus questions per person per day. */
export function UsageFields({
  usage,
  onChange,
}: {
  usage: UsageLimits;
  onChange: (u: UsageLimits) => void;
}) {
  const u = usage;
  const field = (k: keyof UsageLimits, label: string, step = 1) => (
    <label className="block" key={k}>
      <span className="text-[13px] font-medium muted">{label}</span>
      <input
        type="number"
        min={k === "days" ? 1 : 0}
        step={step}
        className="field mt-1"
        value={u[k]}
        onChange={(e) => onChange({ ...u, [k]: num(e.target.value) })}
      />
    </label>
  );
  return (
    <>
      <p className="help !mt-0">
        You can change these later on the agent&apos;s page. It won&apos;t need to be
        tested or approved again. Leave a box at 0 for no limit.
      </p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {field("requests", "Questions in the period")}
        {field("tokens", "Tokens in the period", 10000)}
        {field("cost_usd", "Cost in the period (USD)", 1)}
        {field("days", "Period (days)")}
        {field("per_person_daily", "Questions per person per day")}
      </div>
      <p className="help">
        Cost is Databricks' list price for the model, read from Databricks'
        billing tables; nothing is estimated by the portal. Test runs count too.
      </p>
    </>
  );
}

/** The policy in a few lines, for the agent's page and the approvers. */
export function policyLines(p: SafetyPolicy): [string, string][] {
  const w: Record<string, string> = {
    block: "blocked",
    warn: "flagged and the agent reminded",
    off: "not checked",
    mask: "hidden from the model",
    remove: "removed",
  };
  const own = p.rules.filter((r) => r.text);
  const u = p.usage;
  const lim = [
    u.requests ? `${u.requests.toLocaleString()} questions` : "",
    u.tokens ? `${u.tokens.toLocaleString()} tokens` : "",
    u.cost_usd ? `$${u.cost_usd.toLocaleString()}` : "",
  ].filter(Boolean);
  return [
    [
      "Messages",
      `Attempts to change its rules ${w[p.input.attacks]}; personal data ${w[p.input.personal_data]}; keys ${w[p.input.secrets]}; unsafe requests ${w[p.input.unsafe]}${p.input.max_chars ? `; up to ${p.input.max_chars.toLocaleString()} characters` : ""}`,
    ],
    [
      "Answers",
      `Personal data ${w[p.output.personal_data]}; unsafe content ${p.output.unsafe === "block" ? "held back" : "not checked"}; keys always removed`,
    ],
    [
      "Own rules",
      own.length
        ? own
            .map(
              (r) =>
                `${r.kind === "phrase" ? "“" + r.text + "”" : r.text} (${r.action === "remove" ? "removed" : "blocked"})`,
            )
            .join("; ")
        : "None",
    ],
    [
      "Checked by",
      p.checker
        ? `${p.checker}; if it cannot answer: ${p.on_error === "block" ? "held back" : "let through and recorded"}`
        : "No checker model",
    ],
    [
      "Usage limits",
      `${lim.length ? lim.join(", ") + ` per ${u.days} days` : "No limit per period"}; ${u.per_person_daily ? u.per_person_daily + " questions per person per day" : "no daily limit per person"}`,
    ],
  ];
}
