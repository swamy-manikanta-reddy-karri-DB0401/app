"use client";

import { ReactNode } from "react";
import { Agent } from "@/lib/api";
import { middleShort } from "@/lib/people";
import { ArrowRightIcon, BookIcon, DownloadIcon, LayersIcon, SparkleIcon, UploadIcon } from "./icons";

/** What kind of assistant this is, in words anyone would use. The backend's own
 *  labels ("Multi-agent") are accurate but mean nothing to most people. */
const KIND_PLAIN: Record<string, string> = {
  supervisor: "Uses several tools",
  knowledge: "Answers from documents",
  agent: "Custom assistant",
  custom: "Built in code",
};

/** Label, icon and colour family for each kind, shared by the cards, the
 *  filters and the quick switcher so one kind always looks the same. */
export function kindMeta(kind: string, fallback = ""): { label: string; icon: ReactNode; cls: string } {
  if (kind === "supervisor") return { label: KIND_PLAIN.supervisor, icon: <LayersIcon size={22} />, cls: "kind-supervisor" };
  if (kind === "knowledge") return { label: KIND_PLAIN.knowledge, icon: <BookIcon size={22} />, cls: "kind-knowledge" };
  return { label: KIND_PLAIN[kind] || fallback || KIND_PLAIN.agent, icon: <SparkleIcon size={22} />, cls: "kind-agent" };
}

/** One assistant, as a card you click to start talking to it. */
export function AgentCard({ agent, onOpen }: { agent: Agent; onOpen: (a: Agent) => void }) {
  const disabled = !agent.ready;
  const kind = kindMeta(agent.kind, agent.kind_label);
  const accepts = (agent.accepts || []).filter(Boolean);
  return (
    <button
      type="button"
      onClick={() => onOpen(agent)}
      disabled={disabled}
      className="agent-card"
      aria-label={`${agent.display_name}. ${kind.label}. ${agent.ready ? "Ready to chat" : "Starting up"}`}
    >
      <div className="flex items-start justify-between gap-3">
        <span aria-hidden className={`kind-tile ${kind.cls}`}>
          {kind.icon}
        </span>
        <span className={`status-pill ${agent.ready ? "status-ready" : "status-wait"}`}>
          <span
            aria-hidden
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: "currentColor" }}
          />
          {agent.ready ? "Ready" : "Starting up"}
        </span>
      </div>

      <h3 className="mt-4 line-clamp-2 text-[16px] font-semibold leading-snug tracking-[-0.01em]" title={agent.display_name}>
        {middleShort(agent.display_name)}
      </h3>
      <p className="mt-0.5 text-[13px] font-medium faint">{kind.label}</p>

      <p className="mt-3 line-clamp-3 text-sm muted">
        {agent.blurb || "No description has been added for this one yet."}
      </p>

      {agent.supports_files || agent.output_volume ? (
        <div className="mt-4 flex flex-wrap gap-1.5">
          {agent.supports_files ? (
            <span className="cap" title="You can attach a file to your question">
              <UploadIcon size={13} />
              {accepts.length ? `Send ${accepts.map((a) => a.toUpperCase()).join(", ")}` : "Send files"}
            </span>
          ) : null}
          {agent.output_volume ? (
            <span className="cap" title="It can hand back files for you to download">
              <DownloadIcon size={13} />
              Returns files
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="mt-auto pt-5">
        <div className="flex items-center justify-between border-t pt-4" style={{ borderColor: "var(--line)" }}>
          <span className="text-sm font-semibold" style={{ color: agent.ready ? "var(--brand-deep)" : "var(--ink-faint)" }}>
            {agent.ready ? "Start a conversation" : "Try again in a few minutes"}
          </span>
          {agent.ready ? (
            <span aria-hidden className="go">
              <ArrowRightIcon size={18} />
            </span>
          ) : null}
        </div>
      </div>
    </button>
  );
}
