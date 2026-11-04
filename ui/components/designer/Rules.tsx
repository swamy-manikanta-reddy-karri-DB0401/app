"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DesignerModel, DesignerRules } from "@/lib/api";
import { ErrorBox, Select, Spinner } from "../bits";
import { Dialog } from "../deployer/parts";
import { PlusIcon, ShieldIcon, TrashIcon } from "../icons";

/** Admins decide what the assistant designer may be used for and may build (designer_guard.py): every
 *  message is checked against these before the designer's AI sees it, and every plan before it is built. */
export function DesignerRulesButton({ models }: { models: DesignerModel[] }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="text-[13px] font-medium underline"
        style={{ color: "var(--brand-deep)" }}
        onClick={() => setOpen(true)}
      >
        <span className="inline-flex items-center gap-1">
          <ShieldIcon size={13} /> Rules for the designer
        </span>
      </button>
      {open ? (
        <RulesDialog models={models} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

const num = (v: string, hi: number) =>
  Math.max(0, Math.min(hi, v.trim() === "" ? 0 : Number(v) || 0));

function RulesDialog({
  models,
  onClose,
}: {
  models: DesignerModel[];
  onClose: () => void;
}) {
  const [r, setR] = useState<DesignerRules | null>(null);
  const [canSave, setCanSave] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [host, setHost] = useState("");
  useEffect(() => {
    api
      .designerRules()
      .then((o) => {
        setR(o.rules);
        setCanSave(o.can_save);
      })
      .catch((e) => setErr(e.message));
  }, []);
  const set = (p: Partial<DesignerRules>) => r && setR({ ...r, ...p });

  async function save() {
    if (!r) return;
    setBusy(true);
    setErr("");
    try {
      const o = await api.saveDesignerRules({
        ...r,
        rules: r.rules.filter((x) => x.trim()),
      });
      setR(o.rules);
      onClose();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const toggle = (
    k: "allow_new_tools" | "allow_changes_data",
    label: string,
    help: string,
  ) => (
    <label className="flex cursor-pointer items-start gap-2.5">
      <input
        type="checkbox"
        className="mt-1 h-4 w-4"
        checked={!!r?.[k]}
        onChange={(e) =>
          set({ [k]: e.target.checked } as Partial<DesignerRules>)
        }
      />
      <span>
        <span className="text-[14px] font-medium">{label}</span>
        <span className="block text-[13px] muted">{help}</span>
      </span>
    </label>
  );

  return (
    <Dialog
      title="Rules for the assistant designer"
      sub="The designer only helps design and fix assistants and tools. Every message is checked before its AI sees it, and every plan before it is built."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-quiet" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || !r || !canSave}
            onClick={save}
          >
            {busy ? "Saving…" : "Save rules"}
          </button>
        </>
      }
    >
      <ErrorBox>{err}</ErrorBox>
      {!canSave ? (
        <p className="notice mb-3">
          Rules can only be saved when the portal has its own tables set up. The
          defaults below apply.
        </p>
      ) : null}
      {!r ? (
        <Spinner label="Loading the rules…" />
      ) : (
        <div className="space-y-6">
          <section>
            <p className="label">Your own rules</p>
            <p className="help !mt-0">
              In plain words. Messages that ask for something against them are
              refused, and a plan or a new tool that breaks one cannot be built.
            </p>
            <div className="mt-2 space-y-2">
              {r.rules.map((x, i) => (
                <div key={i} className="flex items-start gap-2">
                  <input
                    className="field min-w-0 flex-1"
                    value={x}
                    maxLength={300}
                    placeholder="For example: never build tools that email people outside the company"
                    onChange={(e) =>
                      set({
                        rules: r.rules.map((y, j) =>
                          j === i ? e.target.value : y,
                        ),
                      })
                    }
                    aria-label={`Rule ${i + 1}`}
                  />
                  <button
                    type="button"
                    className="btn btn-quiet !min-h-[40px] !px-2"
                    aria-label="Remove this rule"
                    onClick={() =>
                      set({ rules: r.rules.filter((_, j) => j !== i) })
                    }
                  >
                    <TrashIcon size={15} />
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="btn btn-quiet"
                disabled={r.rules.length >= 30}
                onClick={() => set({ rules: [...r.rules, ""] })}
              >
                <PlusIcon size={15} /> Add a rule
              </button>
            </div>
          </section>

          <section className="space-y-3">
            <p className="label">What it may build</p>
            {toggle(
              "allow_new_tools",
              "New tools may be written",
              "Off: it only uses tools, data and documents that already exist.",
            )}
            {toggle(
              "allow_changes_data",
              "Tools may save or change things",
              "Off: every new tool is read only. When on, a tool still asks the person each time.",
            )}
            <label className="block max-w-xs">
              <span className="text-[13px] font-medium muted">
                New tools per assistant (0 to 4)
              </span>
              <input
                type="number"
                min={0}
                max={4}
                className="field mt-1"
                value={r.max_new_tools}
                onChange={(e) => set({ max_new_tools: num(e.target.value, 4) })}
              />
            </label>
            <div>
              <span className="text-[13px] font-medium muted">
                Websites a new tool may call
              </span>
              <p className="text-[13px] faint">
                Empty: any website the tool declares (you still read each one
                before it is created).
              </p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {r.allowed_hosts.map((h) => (
                  <span key={h} className="chip">
                    {h}
                    <button
                      type="button"
                      className="ml-1"
                      aria-label={`Remove ${h}`}
                      onClick={() =>
                        set({
                          allowed_hosts: r.allowed_hosts.filter((x) => x !== h),
                        })
                      }
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
              <div className="mt-2 flex gap-2">
                <input
                  className="field min-w-0 flex-1"
                  value={host}
                  onChange={(e) => setHost(e.target.value)}
                  placeholder="api.example.com or *.example.com"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && host.trim()) {
                      set({
                        allowed_hosts: [
                          ...new Set([
                            ...r.allowed_hosts,
                            host.trim().toLowerCase(),
                          ]),
                        ],
                      });
                      setHost("");
                    }
                  }}
                />
                <button
                  type="button"
                  className="btn btn-quiet"
                  disabled={!host.trim()}
                  onClick={() => {
                    set({
                      allowed_hosts: [
                        ...new Set([
                          ...r.allowed_hosts,
                          host.trim().toLowerCase(),
                        ]),
                      ],
                    });
                    setHost("");
                  }}
                >
                  Add
                </button>
              </div>
            </div>
          </section>

          <section className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="text-[13px] font-medium muted">
                Who may use the designer (a team)
              </span>
              <input
                className="field mt-1"
                value={r.group}
                maxLength={128}
                onChange={(e) => set({ group: e.target.value })}
              />
              <span className="help">
                Workspace admins in this team. "admins" means every workspace
                admin.
              </span>
            </label>
            <label className="block">
              <span className="text-[13px] font-medium muted">
                Messages per person per day
              </span>
              <input
                type="number"
                min={0}
                className="field mt-1"
                value={r.turns_per_day}
                onChange={(e) =>
                  set({ turns_per_day: num(e.target.value, 100000) })
                }
              />
              <span className="help">
                0 means no limit. Includes tool tests and changes asked for in
                words.
              </span>
            </label>
            <label className="block sm:col-span-2">
              <span className="text-[13px] font-medium muted">
                Messages are checked by
              </span>
              <Select
                className="mt-1"
                value={r.checker}
                onChange={(v) => set({ checker: v })}
                options={[
                  {
                    value: "",
                    label: "The designer's own model",
                    detail: "The model chosen for the conversation",
                  },
                  ...models.map((m) => ({
                    value: m.name,
                    label: m.label,
                    detail: [m.cost, m.fit].filter(Boolean).join(" · "),
                  })),
                ]}
              />
              <span className="help">
                One short check per typed message (a second or two). Clicking
                one of the designer's own answers is not checked.
              </span>
            </label>
          </section>
        </div>
      )}
    </Dialog>
  );
}
