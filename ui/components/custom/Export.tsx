"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ErrorBox } from "../bits";
import { Dialog } from "../deployer/parts";
import { DownloadIcon } from "../icons";

/** Developer mode: download a custom agent as a code project (agent_export.py). Shown only to developers. */
export function ExportButton({ id, name }: { id: string; name: string }) {
  const [dev, setDev] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    api
      .developerStatus()
      .then((d) => setDev(d.on))
      .catch(() => setDev(false));
  }, []);
  if (!dev) return null;
  return (
    <>
      <button type="button" className="btn btn-quiet" onClick={() => setOpen(true)}>
        <DownloadIcon size={15} /> Export as code
      </button>
      {open ? <ExportDialog id={id} name={name} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

const CHOICES = [
  {
    value: "mlflow",
    title: "Databricks standard (MLflow)",
    detail:
      "An MLflow ResponsesAgent with a plain tool loop. Deploy, trace and evaluate it with Databricks' own tools. Recommended.",
  },
  {
    value: "langgraph",
    title: "LangGraph",
    detail:
      "A LangGraph agent with ChatDatabricks, inside the same MLflow interface. For teams that already build with LangChain or LangGraph.",
  },
] as const;

function ExportDialog({ id, name, onClose }: { id: string; name: string; onClose: () => void }) {
  const [choice, setChoice] = useState<"mlflow" | "langgraph">("mlflow");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function download() {
    setBusy(true);
    setErr("");
    try {
      const { blob, file } = await api.exportCustomAgent(id, choice);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = file;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      onClose();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`Export “${name}” as code`}
      sub="A project your developers own: the agent, its runbook, rules, tools and test conversations, ready to run, change and deploy as a Databricks App."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-quiet" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={download}>
            {busy ? "Preparing…" : "Download the project"}
          </button>
        </>
      }
    >
      <ErrorBox>{err}</ErrorBox>
      <div className="grid gap-2" role="radiogroup" aria-label="How the code is written">
        {CHOICES.map((c) => (
          <label
            key={c.value}
            className="flex cursor-pointer items-start gap-3 rounded-xl p-3"
            style={{ border: `1.5px solid ${choice === c.value ? "var(--brand)" : "var(--line)"}`, background: choice === c.value ? "var(--brand-soft)" : "transparent" }}
          >
            <input type="radio" className="mt-1" name="framework" checked={choice === c.value} onChange={() => setChoice(c.value)} />
            <span>
              <span className="block text-[15px] font-semibold">{c.title}</span>
              <span className="block text-[13.5px] muted">{c.detail}</span>
            </span>
          </label>
        ))}
      </div>
      <ul className="mt-4 list-disc space-y-1 pl-5 text-[13.5px] muted">
        <li>Every call runs as the person asking, as in the portal.</li>
        <li>Tools that change something are not run unless your developers allow it (ALLOW_CHANGES=1).</li>
        <li>No keys are included. The portal&apos;s safety policy, limits, approval and memory stay in the portal; the README says how to add your own.</li>
        <li>Deploy it as a Databricks App and add it back with <b>Add an agent built in code</b>.</li>
      </ul>
    </Dialog>
  );
}
