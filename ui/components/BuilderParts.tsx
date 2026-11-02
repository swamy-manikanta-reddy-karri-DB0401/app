"use client";

import { ReactNode, useEffect, useState } from "react";
import { api, SourceItem } from "@/lib/api";
import { ErrorBox, Select, useLoad } from "./bits";

/** Pieces shared by the assistant wizard and the Genie wizard, so the two look
 *  and behave identically: the step frame, the access step, the review section
 *  and the "what happens next" screen. */

export type Access = { kind: "group" | "user"; principal: string };

export type Finished = {
  kind: "created" | "saved" | "deleted";
  name: string;
  warnings?: string[];
  by?: string;
  access?: number;
  /** "data" is a Genie space; "docs" a Knowledge Assistant; chat says a chat
   *  version was also created (Genie only). */
  variant?: "assistant" | "data" | "docs" | "app";
  chat?: boolean;
  /** Ready-made tools still being deployed; each is added to the assistant when it is up. */
  deploying?: string[];
};

// ----------------------------------------------------------------- done -----

export function Done({
  done,
  onGoto,
  onBack,
  onNew,
}: {
  done: Finished;
  onGoto?: (tab: string) => void;
  onBack: () => void;
  onNew: () => void;
}) {
  const created = done.kind === "created";
  const data = done.variant === "data";
  const docs = done.variant === "docs";
  const chat = !data || !!done.chat; // can people chat with it in this portal?
  const n = done.access || 0;
  const who = n === 1 ? "person or team" : `${n} people and teams`;

  let title = "Changes saved";
  let body = `“${done.name}” is up to date.`;
  if (done.kind === "deleted") {
    title = data ? "Data assistant deleted" : docs ? "Document assistant deleted" : "Assistant deleted";
    body = `“${done.name}” has been removed.`;
  } else if (created && done.variant === "app") {
    title = "Your custom agent is in the portal";
    body = `“${done.name}” now appears under “Your assistants” for the people who may use its app. Nothing in the app itself was changed.`;
  } else if (created && docs) {
    title = "Your document assistant is on its way";
    body = `“${done.name}” has been created. Databricks now reads your documents, which can take several minutes (longer for big folders), before anyone can chat with it.`;
  } else if (created && data && done.chat) {
    title = "Your data assistant is on its way";
    body = `“${done.name}” has been created in Databricks. The chat version needs a few minutes to start before anyone can chat with it here.`;
  } else if (created && data) {
    title = "Your data assistant is ready";
    body = `“${done.name}” has been created in Databricks. People you chose can open it from the Genie page there.`;
  } else if (created) {
    title = "Your assistant is on its way";
    body = `“${done.name}” has been created. It needs a few minutes to start up before anyone can chat with it.`;
  }

  return (
    <div className="mx-auto max-w-4xl">
      <div className="card p-7 text-center">
        <div
          aria-hidden
          className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full text-2xl"
          style={{ background: "var(--brand-soft)", color: "var(--brand-deep)" }}
        >
          ✓
        </div>
        <h2 className="text-xl font-semibold">{title}</h2>
        <p className="mt-2 text-[15px] muted">{body}</p>
      </div>

      {done.warnings?.length ? (
        <div className="mt-4 space-y-2">
          {done.warnings.map((w) => (
            <ErrorBox key={w}>{w}</ErrorBox>
          ))}
        </div>
      ) : null}

      {done.deploying?.length ? (
        <div className="card mt-4 p-6">
          <h3 className="text-[15px] font-semibold">
            {done.deploying.length === 1 ? "Getting a tool ready" : "Getting your tools ready"}
          </h3>
          <p className="mt-2 text-[15px] muted">
            We are getting {done.deploying.join(", ")} ready for this assistant. It is added by itself as soon as it
            is ready, usually within a few minutes. Until then the assistant works without{" "}
            {done.deploying.length === 1 ? "it" : "them"}. If it has not appeared after a while, open the assistant,
            check that it is ticked and save it again.
          </p>
        </div>
      ) : null}

      {created ? (
        <div className="card mt-4 p-6">
          <h3 className="text-[15px] font-semibold">What to do next</h3>
          <ol className="mt-3 space-y-3 text-[15px]">
            <li className="flex gap-3">
              <span className="step-num shrink-0">1</span>
              <span>
                {n && done.variant === "app" ? (
                  <>
                    <b>The {who} you chose can now use it.</b> You can check or change this any time.
                  </>
                ) : n && chat ? (
                  <>
                    <b>Access is being set up.</b> The {who} you chose will be able to use it as soon as it has
                    started. You can check or change this any time.
                  </>
                ) : n ? (
                  <>
                    <b>The {who} you chose can now use it.</b>
                  </>
                ) : chat ? (
                  <>
                    <b>Choose who can use it.</b> Nobody can chat with it until you give them access.
                  </>
                ) : (
                  <>
                    <b>Share it.</b> Nobody else can open it yet. Use the Share button on its Genie page in Databricks.
                  </>
                )}
                {chat && onGoto ? (
                  <>
                    {" "}
                    <button type="button" className="underline" onClick={() => onGoto("access")}>
                      Go to Access
                    </button>
                  </>
                ) : null}
              </span>
            </li>
            {chat ? (
              <li className="flex gap-3">
                <span className="step-num shrink-0">2</span>
                <span>
                  <b>Try it out.</b>{" "}
                  {done.variant === "app" ? "It is already under “Your assistants”." : "After a few minutes it appears under “Your assistants”."}
                  {onGoto ? (
                    <>
                      {" "}
                      <button type="button" className="underline" onClick={() => onGoto("agents")}>
                        Go to “Your assistants”
                      </button>
                    </>
                  ) : null}
                </span>
              </li>
            ) : null}
            {docs ? (
              <li className="flex gap-3">
                <span className="step-num shrink-0">3</span>
                <span>
                  <b>Check people can read the documents.</b> They may also need permission to read the folders you chose.
                  If an answer says it cannot see the documents, ask your data team.
                </span>
              </li>
            ) : null}
            {data ? (
              <li className="flex gap-3">
                <span className="step-num shrink-0">{chat ? 3 : 2}</span>
                <span>
                  <b>Check they can read the data.</b> People also need permission to read the tables you chose.
                  This portal cannot grant that. Ask your data team if someone sees “no access”.
                </span>
              </li>
            ) : null}
          </ol>
          {done.by ? <p className="help mt-4">Created by {done.by}.</p> : null}
        </div>
      ) : null}

      <div className="mt-6 flex flex-wrap justify-center gap-3">
        <button type="button" className="btn btn-primary" onClick={onBack}>
          Back to my assistants
        </button>
        {created ? (
          <button type="button" className="btn btn-quiet" onClick={onNew}>
            Create another
          </button>
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- frame -----

/** The chrome around every wizard: steps down the left on wide screens (wrapped
 *  across the top on narrow ones), the form in the remaining width, and the
 *  Back / Next / finish buttons underneath. */
export function WizardFrame({
  title,
  steps,
  step,
  reached,
  editing,
  onGo,
  err,
  problem,
  busy,
  finishLabel,
  finishDisabled,
  onFinish,
  onCancel,
  onDelete,
  deleteLabel,
  status,
  children,
}: {
  title: string;
  steps: { key: string; title: string }[];
  step: number;
  reached: number;
  editing: boolean;
  onGo: (i: number) => void;
  err: string;
  problem: string;
  busy: boolean;
  finishLabel: string;
  finishDisabled: boolean;
  onFinish: () => void;
  onCancel: () => void;
  onDelete?: () => void;
  deleteLabel?: string;
  /** Shown under the buttons: what is going on while the person waits. */
  status?: ReactNode;
  children: ReactNode;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const last = steps.length - 1;
  const pct = Math.round(((step + 1) / steps.length) * 100);

  function go(i: number) {
    onGo(i);
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }

  return (
    <div className="w-full">
      <div className="mb-5 flex items-center gap-3">
        <button type="button" onClick={onCancel} className="btn btn-quiet">
          ← Cancel
        </button>
        <h2 className="text-xl font-semibold">{title}</h2>
      </div>

      <div className="lg:grid lg:grid-cols-[260px_minmax(0,1fr)] lg:items-start lg:gap-10">
        <aside className="lg:sticky lg:top-24">
          <p className="text-sm muted" aria-live="polite">
            Step {step + 1} of {steps.length} · <b>{steps[step].title}</b>
          </p>
          <div className="progress mt-2" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            <span style={{ width: pct + "%" }} />
          </div>
          <ol className="mt-3 flex flex-wrap gap-2 lg:flex-col lg:flex-nowrap" aria-label="Steps">
            {steps.map((x, i) => (
              <li key={x.key} className="lg:w-full">
                <button
                  type="button"
                  className="step-pill lg:w-full lg:!rounded-xl lg:!px-4"
                  aria-current={i === step ? "step" : undefined}
                  data-done={i < step || (editing && i !== step) ? "true" : "false"}
                  disabled={!editing && i > reached}
                  onClick={() => go(i)}
                >
                  <span className="step-num">{i < step ? "✓" : i + 1}</span>
                  {x.title}
                </button>
              </li>
            ))}
          </ol>
        </aside>

        <div className="mt-5 min-w-0 lg:mt-0">
          <ErrorBox>{err}</ErrorBox>

          <div className="card mt-3 p-6 lg:p-8">{children}</div>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            {step > 0 ? (
              <button type="button" className="btn btn-quiet" onClick={() => go(step - 1)} disabled={busy}>
                ← Back
              </button>
            ) : null}
            {step < last ? (
              <button type="button" className="btn btn-primary btn-lg" onClick={() => go(step + 1)} disabled={!!problem}>
                Next →
              </button>
            ) : (
              <button type="button" className="btn btn-primary btn-lg" onClick={onFinish} disabled={busy || finishDisabled}>
                {busy ? "Working…" : finishLabel}
              </button>
            )}

            {problem && step < last ? (
              <span className="text-sm muted" role="status">
                {problem}
              </span>
            ) : null}

            {onDelete && step === last ? (
              confirmDelete ? (
                <span className="inline-flex flex-wrap items-center gap-2 text-sm">
                  {(deleteLabel || "Delete") + " for good?"}
                  <button type="button" className="btn btn-quiet" onClick={onDelete} disabled={busy}>
                    Yes, delete it
                  </button>
                  <button type="button" className="btn btn-quiet" onClick={() => setConfirmDelete(false)}>
                    No, keep it
                  </button>
                </span>
              ) : (
                <button type="button" className="btn btn-quiet ml-auto" onClick={() => setConfirmDelete(true)}>
                  {deleteLabel || "Delete"}
                </button>
              )
            ) : null}
          </div>

          {status}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------- access -------

/** Teams and people who may chat with the new assistant. The lists and the level
 *  ("can use") are exactly what People & access offers; the grant is applied once
 *  Databricks has started the assistant, because its endpoint does not exist yet. */
export function AccessStep({
  access,
  setAccess,
  note,
}: {
  access: Access[];
  setAccess: (a: Access[]) => void;
  note?: string;
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.builderPrincipals>> | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [kind, setKind] = useState<"group" | "user">("group");
  const [pick, setPick] = useState("");
  const [typed, setTyped] = useState("");

  useEffect(() => {
    api.builderPrincipals().then(setData).catch((e) => setLoadErr(e.message));
  }, []);

  const has = (k: string, p: string) => access.some((a) => a.kind === k && a.principal === p);

  const options = (kind === "group"
    ? (data?.groups || []).map((g) => ({ value: g.name, label: g.name }))
    : (data?.users || []).map((u) => ({ value: u.name, label: u.display || u.name, detail: u.display && u.display !== u.name ? u.name : undefined }))
  ).map((o) => (has(kind, o.value) ? { ...o, disabled: true, note: "Added" } : o));

  function add(p: string) {
    const who = p.trim();
    if (!who || has(kind, who)) return;
    setAccess([...access, { kind, principal: who }]);
    setPick("");
    setTyped("");
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">Who should be able to use it?</h3>
        <p className="mt-1 text-[15px] muted">
          Choose the teams or people who can chat with it. You can also skip this and do it later
          on the Access page.
        </p>
      </div>

      {access.length ? (
        <ul className="flex flex-wrap gap-2" aria-label="Chosen teams and people">
          {access.map((a) => (
            <li key={a.kind + a.principal} className="tag !min-h-[36px] gap-2 !text-sm">
              <span className="faint">{a.kind === "group" ? "Team" : "Person"}</span>
              {a.principal}
              <button
                type="button"
                aria-label={`Remove ${a.principal}`}
                className="opacity-70 hover:opacity-100"
                onClick={() => setAccess(access.filter((x) => x !== a))}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="rounded-xl p-4 text-[15px] muted" style={{ background: "var(--canvas)" }}>
          Nobody chosen yet.
        </p>
      )}

      <div className="rounded-xl p-5" style={{ border: "1px solid var(--line)", background: "var(--canvas)" }}>
        <h4 className="text-[15px] font-semibold">Add a team or a person</h4>
        {loadErr ? <p className="help">Could not load the list ({loadErr}). You can type a name below.</p> : null}
        <div className="mt-3 grid gap-3 md:grid-cols-[220px_minmax(0,1fr)_auto] md:items-end">
          <label className="block">
            <span className="text-[13px] font-medium muted">Who</span>
            <Select
              className="mt-1"
              value={kind}
              onChange={(v) => {
                setKind(v as "group" | "user");
                setPick("");
              }}
              options={[
                { value: "group", label: "A team (group)" },
                { value: "user", label: "One person" },
              ]}
            />
          </label>
          <label className="block min-w-0">
            <span className="text-[13px] font-medium muted">Choose</span>
            <Select
              className="mt-1"
              value={pick}
              onChange={setPick}
              options={options}
              loading={data === null && !loadErr}
              placeholder={kind === "group" ? "Choose a team…" : "Choose a person…"}
              empty={kind === "group" ? "No teams found" : "No people found"}
            />
          </label>
          <button type="button" className="btn btn-primary" onClick={() => add(pick)} disabled={!pick}>
            Add
          </button>
        </div>

        <details className="mt-3">
          <summary className="cursor-pointer text-[13px] font-medium muted">
            Advanced: type the exact name or email instead
          </summary>
          <div className="mt-2 flex flex-wrap gap-3">
            <input
              className="field max-w-md flex-1"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={kind === "group" ? "exact team name" : "name@company.com"}
            />
            <button type="button" className="btn btn-quiet" onClick={() => add(typed)} disabled={!typed.trim()}>
              Add this
            </button>
          </div>
        </details>
      </div>

      <p className="help">
        {note ||
          "They can use it, but not change it. The access is applied a few minutes after you create the assistant, once Databricks has started it."}
      </p>
    </div>
  );
}

export function Section({
  title,
  onChange,
  children,
}: {
  title: string;
  onChange: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl p-4" style={{ border: "1px solid var(--line)" }}>
      <div className="mb-2 flex items-center justify-between gap-3">
        <h4 className="text-[13px] font-semibold uppercase tracking-wide faint">{title}</h4>
        <button type="button" className="btn btn-quiet !min-h-[36px] !px-3" onClick={onChange}>
          Change
        </button>
      </div>
      {children}
    </section>
  );
}

/** catalog -> schema -> folder browser that also accepts a typed value. */
export function VolumeField({
  label,
  hint,
  value,
  suffix,
  onChange,
}: {
  label: string;
  hint: string;
  value: string;
  suffix: string;
  onChange: (v: string) => void;
}) {
  const [catalog, setCatalog] = useState("");
  const [schema, setSchema] = useState("");
  useEffect(() => setSchema(""), [catalog]);

  const catsL = useLoad(() => api.builderSources("catalogs"), []);
  const schsL = useLoad(catalog ? () => api.builderSources("schemas", catalog) : null, [catalog]);
  const volsL = useLoad(catalog && schema ? () => api.builderSources("volume", catalog, schema) : null, [catalog, schema]);
  const catalogs = catsL.data?.items || [];
  const schemas = schsL.data?.items || [];
  const volumes = volsL.data?.items || [];

  const path = value.split(".").length === 3 ? "/Volumes/" + value.split(".").join("/") + suffix : "";

  return (
    <div>
      <p className="label">{label}</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="text-[13px] font-medium muted">1. Catalog</span>
          <Select className="mt-1" value={catalog} onChange={setCatalog} options={catalogs} loading={catsL.loading} />
        </label>
        <label className="block">
          <span className="text-[13px] font-medium muted">2. Schema</span>
          <Select
            className="mt-1"
            value={schema}
            onChange={setSchema}
            options={schemas}
            disabled={!catalog}
            loading={schsL.loading}
            placeholder={catalog ? "Choose…" : "Pick a catalog first"}
          />
        </label>
        <label className="block">
          <span className="text-[13px] font-medium muted">3. Folder</span>
          <Select
            className="mt-1"
            value=""
            onChange={(v) => v && onChange(v)}
            options={volumes}
            disabled={!schema}
            loading={volsL.loading}
            placeholder={schema ? "Choose…" : "Pick a schema first"}
            empty="No folders here"
          />
        </label>
      </div>

      {/* Why a list is empty (no permission, not found), in words, instead of a bare "No folders here". */}
      {[catsL.data?.note, catalog ? schsL.data?.note : "", schema ? volsL.data?.note : ""].filter(Boolean).slice(-1).map((n) => (
        <p key={n} className="mt-2 rounded-lg px-3 py-2 text-[13px]" style={{ background: "var(--warn-bg)" }} role="status">
          {n}
        </p>
      ))}
      {value ? (
        <p className="mt-3 rounded-lg px-3 py-2 text-[14px]" style={{ background: "var(--brand-soft)" }}>
          <b>Chosen:</b> {value}
          {path ? <span className="block break-all text-[13px] muted">Files go to {path}</span> : null}
          <button type="button" className="ml-0 mt-1 block text-[13px] underline" onClick={() => onChange("")}>
            Clear
          </button>
        </p>
      ) : null}

      <details className="mt-2">
        <summary className="cursor-pointer text-[13px] font-medium muted">Advanced: type the exact name instead</summary>
        <input
          className="field mt-2"
          value={value}
          onChange={(e) => onChange(e.target.value.trim())}
          placeholder="catalog.schema.volume"
        />
      </details>
      <p className="help">{hint}</p>
    </div>
  );
}
