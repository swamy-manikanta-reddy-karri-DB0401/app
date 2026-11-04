"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ErrorBox, Spinner } from "../bits";
import { Dialog } from "../deployer/parts";
import { BookIcon, TrashIcon } from "../icons";

/** What a custom agent remembers about the person using it, shown to that person only (memory.py). */
export function MemoryButton({ agentKey }: { agentKey: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="icon-btn"
        onClick={() => setOpen(true)}
        title="What it remembers about you"
        aria-label="What it remembers about you"
      >
        <BookIcon size={16} />
      </button>
      {open ? (
        <MemoryDialog agentKey={agentKey} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

function MemoryDialog({
  agentKey,
  onClose,
}: {
  agentKey: string;
  onClose: () => void;
}) {
  const [notes, setNotes] = useState<
    { id: string; text: string; at: string }[] | null
  >(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const load = () =>
    api
      .myMemory(agentKey)
      .then((r) => setNotes(r.notes))
      .catch((e) => setErr(e.message));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentKey]);

  async function forget(id = "") {
    setBusy(id || "all");
    setErr("");
    try {
      await api.forgetMemory(agentKey, id);
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <Dialog
      title="What it remembers about you"
      sub="Short notes from your earlier chats, so it does not ask again. Only you can see them, and you can remove any of them."
      onClose={onClose}
      footer={
        <>
          {notes && notes.length ? (
            <button
              type="button"
              className="btn btn-quiet"
              disabled={!!busy}
              onClick={() => forget()}
            >
              {busy === "all" ? "Forgetting…" : "Forget everything"}
            </button>
          ) : null}
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Done
          </button>
        </>
      }
    >
      <ErrorBox>{err}</ErrorBox>
      {!notes && !err ? <Spinner label="Loading…" /> : null}
      {notes && !notes.length ? (
        <p className="text-[14px] muted">
          Nothing yet. It keeps facts you tell it, like your currency or your
          team.
        </p>
      ) : null}
      {notes && notes.length ? (
        <ul className="divide-y divide-[color:var(--line)]">
          {notes.map((n) => (
            <li key={n.id} className="flex items-start gap-3 py-2.5">
              <span className="min-w-0 flex-1 break-words text-[14px]">
                {n.text}
              </span>
              <button
                type="button"
                className="icon-btn !h-8 !w-8"
                disabled={!!busy}
                onClick={() => forget(n.id)}
                title="Forget this"
                aria-label={`Forget: ${n.text}`}
              >
                <TrashIcon size={14} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </Dialog>
  );
}
