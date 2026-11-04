"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { ErrorBox, Spinner } from "../bits";
import { CheckIcon, UploadIcon } from "../icons";

/** Developer mode: start an agent from code. Upload a project (for example one exported here and changed); the
 *  portal checks it, deploys it as a Databricks App and offers it as an assistant (code_upload.py). */
export function CodeUpload({ onCancel, onDone }: { onCancel: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [appName, setAppName] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<{ state: string; step: string; app_name: string; note?: string; new?: boolean } | null>(null);
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
    },
    []
  );

  async function upload() {
    if (!file) return;
    setBusy(true);
    setErr("");
    try {
      const started = await api.uploadCodeAgent(file, name, desc, appName);
      setJob({ state: "deploying", step: "Copying the files.", app_name: started.app_name, new: started.new });
      for (let i = 0; i < 160 && alive.current; i++) {
        await new Promise((r) => setTimeout(r, 6000));
        const j = await api.codeAgentJob(started.job);
        if (!alive.current) return;
        setJob(j);
        if (j.state !== "deploying") break;
      }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-3xl">
      <div className="card p-6">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-xl font-semibold">Upload an agent&apos;s code</h2>
          <span className="rounded-full px-2 py-0.5 text-[11px] font-semibold" style={{ background: "var(--brand-soft)", color: "var(--brand-deep)" }}>
            Developer mode
          </span>
        </div>
        <p className="mt-1 text-[15px] muted">
          A ZIP of an agent project, for example one you exported from a custom agent and changed. The portal checks the files, deploys
          them as a Databricks App and offers it as an assistant. It never runs your code itself.
        </p>
        <ErrorBox>{err}</ErrorBox>

        {!job ? (
          <div className="mt-5 space-y-4">
            <label className="block">
              <span className="text-[13px] font-medium muted">The project (.zip)</span>
              <span className="mt-1 flex flex-wrap items-center gap-3">
                <span className="btn btn-quiet cursor-pointer">
                  <UploadIcon size={15} /> {file ? "Choose another" : "Choose a ZIP"}
                  <input type="file" accept=".zip" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] || null)} />
                </span>
                {file ? <span className="text-[14px]">{file.name}</span> : null}
              </span>
            </label>
            <label className="block">
              <span className="text-[13px] font-medium muted">Name people will see</span>
              <input className="field mt-1" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="e.g. Media Plan Builder" />
            </label>
            <label className="block">
              <span className="text-[13px] font-medium muted">What it does (one sentence)</span>
              <input className="field mt-1" value={desc} maxLength={300} onChange={(e) => setDesc(e.target.value)} />
            </label>
            <label className="block max-w-sm">
              <span className="text-[13px] font-medium muted">App name (optional)</span>
              <input
                className="field mt-1 font-mono text-[13px]"
                value={appName}
                maxLength={30}
                onChange={(e) => setAppName(e.target.value.toLowerCase())}
                placeholder="made from the name, e.g. agent-media-plan-builder"
              />
              <span className="help">Uploading again with the same app name deploys a new version.</span>
            </label>
            <ul className="list-disc space-y-1 pl-5 text-[13.5px] muted">
              <li>Needs an app.yaml at the top (an exported project has one). Source and text files only, up to 20 MB.</li>
              <li>Files that look like keys, and any key inside a file, are refused. Keys belong in the secret scope.</li>
              <li>People reach it with their own sign-in. Share it on Access once it is running.</li>
              <li>Each agent is one Databricks App, and a workspace can have 100 apps.</li>
            </ul>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-primary" disabled={busy || !file || name.trim().length < 2} onClick={upload}>
                {busy ? "Checking…" : "Upload and deploy"}
              </button>
              <button type="button" className="btn btn-quiet" onClick={onCancel}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-5">
            {job.state === "deploying" ? (
              <>
                <Spinner label={`Deploying ${job.app_name}… ${job.step}`} />
                <p className="help">This usually takes a few minutes. You can leave this page; it carries on.</p>
              </>
            ) : job.state === "done" ? (
              <>
                <p className="flex items-center gap-2 text-[15px]">
                  <span style={{ color: "var(--ok)" }} aria-hidden>
                    <CheckIcon size={18} />
                  </span>
                  <b>{job.app_name} is running and offered as an assistant.</b>
                </p>
                {job.note ? <p className="notice mt-3">{job.note}</p> : null}
                <p className="mt-2 text-[14px] muted">Give people access on the Access page. Until then only you can use it.</p>
                <button type="button" className="btn btn-primary mt-4" onClick={onDone}>
                  Done
                </button>
              </>
            ) : (
              <>
                <ErrorBox>The deployment did not finish: {job.step}</ErrorBox>
                <div className="mt-3 flex gap-2">
                  <button type="button" className="btn btn-quiet" onClick={() => setJob(null)}>
                    Back
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
