"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Agent, api, Approval, ApprovalItem, downloadUrl, FileRef, Reply, SavedChat, upload } from "@/lib/api";
import { ErrorBox } from "./bits";
import { ChatHistory } from "./ChatHistory";
import {
  ArrowUpIcon,
  CheckIcon,
  ChevronLeftIcon,
  CopyIcon,
  PanelLeftIcon,
  PaperclipIcon,
  RefreshIcon,
  SparkleIcon,
} from "./icons";

type Turn = {
  role: "user" | "assistant" | "error";
  text: string;
  tools?: string[];
  citations?: { label: string; url: string }[];
  attachments?: FileRef[];
  files?: string[];
  /** A tool call waiting for the person; `sent` is the history it was asked with. */
  approval?: Approval;
  sent?: { role: string; content: string }[];
  decided?: "approved" | "declined";
};

/** What the last question was, so a failed one can be sent again unchanged. */
type Attempt = { text: string; files: string[]; shown: string[] };

const MAX_INPUT_PX = 200;

/** A conversation id the server can key saved history on. randomUUID needs a
 *  secure context (https or localhost), so there is a fallback for plain http. */
function newId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 3) | 8).toString(16);
    });
  }
}

export function Chat({
  agent,
  agents,
  historyEnabled,
  resumeId = "",
  onBack,
  onSwitch,
}: {
  agent: Agent;
  agents: Agent[];
  historyEnabled: boolean;
  /** Open this saved conversation straight away (from "Recent" on the home page). */
  resumeId?: string;
  onBack: () => void;
  onSwitch: (a: Agent) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<{ name: string; path: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState("");
  const [err, setErr] = useState("");
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  // Set once the first question is sent (or an old conversation is opened);
  // the server files every later message under it.
  const [conversationId, setConversationId] = useState("");
  // Small screens: the list is a panel above the chat, toggled from the top bar.
  const [showHistory, setShowHistory] = useState(false);
  // Wide screens: fold the list down to a slim rail to give the conversation the
  // room. Remembered, because it is a preference about the screen, not a chat.
  const [collapsed, setCollapsed] = useState(false);
  const [saved, setSaved] = useState<SavedChat[] | null>(null);
  const [savedErr, setSavedErr] = useState("");

  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Follow new messages only while the reader is already at the bottom. Yanking
  // the view down while someone scrolls back to re-read an earlier answer is
  // the most annoying thing a chat can do.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  function onScroll() {
    const el = scrollRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  // A different assistant means a different conversation. Carrying the old
  // transcript across would send one model's words to another as if it had
  // said them.
  useEffect(() => {
    setTurns([]);
    setPending([]);
    setDraft("");
    setErr("");
    setAttempt(null);
    setConversationId("");
    setShowHistory(false);
    stickRef.current = true;
    inputRef.current?.focus();
  }, [agent.name]);

  // Declared after the reset above, so on open the reset runs first and the
  // requested conversation is then loaded on top of the empty chat.
  useEffect(() => {
    if (resumeId && historyEnabled) resume(resumeId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.name, resumeId]);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem("agent-portal-chats-collapsed") === "1");
    } catch {
      // Blocked storage just means the list opens expanded each visit.
    }
  }, []);

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem("agent-portal-chats-collapsed", next ? "1" : "0");
    } catch {
      // Not worth an error; it only resets on the next visit.
    }
  }

  // Load this person's past chats for this agent as soon as the chat opens, so
  // the list is already there rather than behind a button.
  useEffect(() => {
    if (!historyEnabled) return;
    let live = true;
    setSaved(null);
    setSavedErr("");
    api
      .chats(agent.name)
      .then((r) => live && setSaved(r.chats))
      .catch((e) => {
        if (!live) return;
        setSavedErr(e.message);
        setSaved([]);
      });
    return () => {
      live = false;
    };
  }, [agent.name, historyEnabled]);

  // Grow with the text up to a cap, then scroll inside the box.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, MAX_INPUT_PX) + "px";
  }, [draft]);

  // Only the real back-and-forth goes to the agent; errors stay local so a
  // failed attempt never poisons the next one.
  const history = () =>
    turns
      .filter((t) => t.role !== "error" && !t.approval)
      .map((t) => ({ role: t.role, content: t.text }));

  async function run(a: Attempt, retry: boolean) {
    setErr("");
    stickRef.current = true;
    setAttempt(a);

    // On a retry the question is already in the transcript, so it is not added
    // (or sent) a second time; only the error under it is removed.
    const base = history();
    const messages = retry ? base : [...base, { role: "user", content: a.text }];
    if (retry) {
      setTurns((t) => (t.length && t[t.length - 1].role === "error" ? t.slice(0, -1) : t));
    } else {
      setTurns((t) => [...t, { role: "user", text: a.text, files: a.shown }]);
    }

    const cid = conversationId || newId();
    if (!conversationId) setConversationId(cid);

    setBusy(true);
    try {
      const out: Reply = await api.chat(agent.name, messages, a.files, cid, a.shown);
      setTurns((t) => [...t, replyTurn(out, messages)]);
      // A new or continued chat appears in the list straight away. The server
      // saves in the background, so re-reading the list here could miss it.
      if (historyEnabled) {
        const title = (messages[0]?.content || "Conversation").replace(/\s+/g, " ").trim();
        const item: SavedChat = {
          id: cid,
          endpoint: agent.name,
          updated: new Date().toISOString(),
          count: messages.length + 1,
          title: title.length > 80 ? title.slice(0, 80) + "…" : title,
        };
        setSaved((l) => [item, ...(l || []).filter((c) => c.id !== cid)]);
      }
    } catch (e: any) {
      setTurns((t) => [...t, { role: "error", text: e.message }]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  // The person approved or declined what an answer was waiting on: the same answer carries on.
  async function decide(index: number, yes: boolean) {
    const turn = turns[index];
    if (!turn?.approval || turn.decided || busy) return;
    setTurns((t) => t.map((x, i) => (i === index ? { ...x, decided: yes ? "approved" : "declined" } : x)));
    stickRef.current = true;
    setBusy(true);
    try {
      const decisions = Object.fromEntries(turn.approval.items.map((it) => [it.id, yes]));
      const out = await api.chatDecide(agent.name, turn.sent || [], turn.approval, decisions, conversationId || undefined);
      setTurns((t) => [...t, replyTurn(out, turn.sent || [])]);
    } catch (e: any) {
      setTurns((t) => [...t, { role: "error", text: e.message }]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function send() {
    const text = draft.trim();
    if ((!text && pending.length === 0) || busy) return;
    const a: Attempt = {
      text: text || "Please process the attached file.",
      files: pending.map((f) => f.path),
      shown: pending.map((f) => f.name),
    };
    setDraft("");
    setPending([]);
    run(a, false);
  }

  function newChat() {
    setTurns([]);
    setPending([]);
    setDraft("");
    setErr("");
    setAttempt(null);
    setConversationId("");
    setShowHistory(false);
    inputRef.current?.focus();
  }

  async function resume(id: string) {
    setErr("");
    try {
      const { messages } = await api.chatOpen(id);
      setTurns(messages.map((m) => ({ ...m })));
      setConversationId(id);
      setAttempt(null);
      setPending([]);
      setShowHistory(false);
      stickRef.current = true;
      inputRef.current?.focus();
    } catch (e: any) {
      setErr(e.message);
    }
  }

  async function forget(id: string) {
    try {
      await api.chatDelete(id);
      setSaved((l) => (l || []).filter((c) => c.id !== id));
      if (id === conversationId) newChat();
    } catch (e: any) {
      setSavedErr(e.message);
    }
  }

  async function pick(f: File | undefined) {
    if (!f) return;
    setErr("");
    setUploading(f.name);
    try {
      const done = await upload(agent.name, f);
      setPending((p) => [...p, { name: done.name, path: done.path }]);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setUploading("");
    }
  }

  const accepts = (agent.accepts || []).map((a) => "." + a.replace(/^\./, ""));
  // While an answer waits for a decision, the next question would leave it hanging.
  const waiting = turns.some((t) => t.approval && !t.decided);
  const canSend = !busy && !waiting && (!!draft.trim() || pending.length > 0);
  const empty = turns.length === 0 && !busy;

  // The same input serves both layouts: centred under the greeting on a fresh
  // chat, pinned to the bottom once there is a conversation.
  const composer = (
    <div className="w-full">
      <div
        className="flex flex-col rounded-3xl px-2 py-2 shadow-sm transition focus-within:border-[var(--brand)]"
        style={{ background: "var(--surface)", border: "1px solid var(--line)" }}
      >
        {pending.length > 0 || uploading ? (
          <div className="flex flex-wrap items-center gap-2 px-2 pb-1 pt-1">
            {pending.map((f, i) => (
              <span key={i} className="tag gap-1.5">
                <PaperclipIcon size={13} /> {f.name}
                <button
                  type="button"
                  aria-label={`Remove ${f.name}`}
                  className="opacity-70 hover:opacity-100"
                  onClick={() => setPending((p) => p.filter((_, j) => j !== i))}
                >
                  ✕
                </button>
              </span>
            ))}
            {uploading ? <span className="text-xs faint">Uploading {uploading}…</span> : null}
          </div>
        ) : null}

        <div className="flex items-end gap-1">
          {agent.supports_files ? (
            <>
              <button
                type="button"
                className="inline-flex h-11 shrink-0 items-center gap-1.5 rounded-full px-3 text-sm font-medium transition hover:bg-[var(--bubble)] disabled:opacity-50"
                style={{ color: "var(--ink-dim)" }}
                onClick={() => fileRef.current?.click()}
                disabled={!!uploading}
                title={accepts.length ? `Attach a file (${accepts.join(", ")})` : "Attach a file"}
                aria-label="Attach a file"
              >
                <PaperclipIcon size={20} />
                <span className="hidden sm:inline">Attach a file</span>
              </button>
              <input
                ref={fileRef}
                type="file"
                className="hidden"
                accept={accepts.join(",") || undefined}
                aria-label="Choose a file to send to this agent"
                onChange={(e) => {
                  pick(e.target.files?.[0]);
                  e.currentTarget.value = "";
                }}
              />
            </>
          ) : null}

          <textarea
            ref={inputRef}
            className="max-h-[200px] min-h-[44px] w-full resize-none bg-transparent px-2 py-3 text-base outline-none placeholder:text-[var(--ink-faint)]"
            rows={1}
            value={draft}
            aria-label="Your message"
            placeholder="Type your question here…"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // isComposing: Enter confirms an IME candidate (Japanese, Chinese,
              // Korean input) and must not also send the half-typed message.
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
          />

          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            className="inline-flex h-11 shrink-0 items-center justify-center gap-1.5 rounded-full px-4 text-sm font-semibold transition"
            style={
              canSend
                ? { background: "var(--brand)", color: "var(--brand-ink)" }
                : { background: "var(--bubble)", color: "var(--ink-faint)" }
            }
            title="Send"
            aria-label="Send message"
          >
            <span className="hidden sm:inline">Send</span>
            <ArrowUpIcon size={18} />
          </button>
        </div>
      </div>
      <p className="mt-2 text-center text-xs faint">
        Press Enter to send · Shift+Enter for a new line
        {agent.supports_files
          ? accepts.length
            ? ` · You can attach: ${accepts.join(", ")}`
            : " · You can attach any kind of file"
          : ""}
      </p>
    </div>
  );

  return (
    <div
      className="flex flex-col md:flex-row"
      style={{ height: "calc(100dvh - var(--header-h, 4rem))", minHeight: 460 }}
    >
      {historyEnabled ? (
        <ChatHistory
          className={showHistory ? "max-h-64 border-b md:max-h-none md:border-b-0" : "hidden md:flex"}
          items={saved}
          error={savedErr}
          activeId={conversationId}
          busy={busy}
          onOpen={resume}
          onNew={newChat}
          onDelete={forget}
          collapsed={collapsed}
          onToggle={toggleCollapsed}
        />
      ) : null}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <div className="flex h-12 shrink-0 items-center gap-2 px-3">
          {historyEnabled ? (
            <button
              type="button"
              className="icon-btn md:hidden"
              onClick={() => setShowHistory(!showHistory)}
              aria-expanded={showHistory}
              title={showHistory ? "Hide your chats" : "Show your chats"}
              aria-label={showHistory ? "Hide your chats" : "Show your chats"}
            >
              <PanelLeftIcon />
            </button>
          ) : null}
          <button
            type="button"
            onClick={onBack}
            className="inline-flex shrink-0 items-center gap-1 rounded-lg py-1.5 pl-1.5 pr-2.5 text-sm muted transition hover:bg-[var(--bubble)] hover:text-[var(--ink)]"
            title="Back to all assistants"
          >
            <ChevronLeftIcon size={16} />
            All assistants
          </button>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[15px] font-semibold leading-tight">
              {agent.display_name}
            </h2>
            <p className="hidden truncate text-xs faint sm:block">
              {agent.blurb || agent.kind_hint}
            </p>
          </div>
          {!historyEnabled && turns.length > 0 ? (
            <button
              type="button"
              className="icon-btn"
              onClick={newChat}
              disabled={busy}
              title="New chat"
              aria-label="New chat"
            >
              <RefreshIcon />
            </button>
          ) : null}
        </div>

        {err ? (
          <div className="mx-auto w-full max-w-5xl shrink-0 px-4">
            <ErrorBox>{err}</ErrorBox>
          </div>
        ) : null}

        {empty ? (
          <div className="relative flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-4 pb-16">
            <div
              aria-hidden
              className="mb-4 flex h-12 w-12 items-center justify-center rounded-full"
              style={{ background: "var(--brand-soft)", color: "var(--brand-deep)" }}
            >
              <SparkleIcon size={24} />
            </div>
            <h1 className="text-center text-2xl font-semibold tracking-tight">
              {agent.display_name}
            </h1>
            <p className="mt-2 max-w-md text-center text-sm muted">
              {agent.blurb ||
                (agent.supports_files
                  ? "Attach a file, or just type your question to get started."
                  : "Type a question to get started.")}
            </p>
            <ol className="mt-6 flex max-w-2xl flex-col gap-2 text-sm muted sm:flex-row sm:gap-8">
              <li className="flex items-center gap-2 whitespace-nowrap">
                <span className="step-num">1</span> Type your question
              </li>
              {agent.supports_files ? (
                <li className="flex items-center gap-2 whitespace-nowrap">
                  <span className="step-num">2</span> Attach a file if needed
                </li>
              ) : null}
              <li className="flex items-center gap-2 whitespace-nowrap">
                <span className="step-num">{agent.supports_files ? 3 : 2}</span>{" "}
                {agent.output_volume ? "Download your result" : "Read the answer"}
              </li>
            </ol>
            <div className="mt-8 w-full max-w-5xl">{composer}</div>
          </div>
        ) : (
          <>
            <div
              ref={scrollRef}
              onScroll={onScroll}
              className="relative min-h-0 flex-1 overflow-y-auto"
              role="log"
              aria-live="polite"
              aria-label="Conversation"
            >
              <div className="mx-auto flex w-full max-w-5xl flex-col gap-7 px-4 py-6">
                {turns.map((t, i) => (
                  <Message
                    key={i}
                    turn={t}
                    agent={agent}
                    onDecide={t.approval && !t.decided && !busy ? (yes) => decide(i, yes) : undefined}
                    onRetry={
                      t.role === "error" && i === turns.length - 1 && attempt && !busy
                        ? () => run(attempt, true)
                        : undefined
                    }
                  />
                ))}
                {busy ? <Typing /> : null}
              </div>
            </div>
            <div className="mx-auto w-full max-w-5xl shrink-0 px-4 pb-3 pt-1">{composer}</div>
          </>
        )}
      </div>
    </div>
  );
}

/** Three pulsing dots beside the assistant's mark: the reply is on its way. */
function Typing() {
  return (
    <div className="flex gap-3" role="status" aria-label="The assistant is working on a reply">
      <Avatar />
      <div className="pt-1.5">
        <div className="flex items-center gap-1.5">
          <span className="typing-dot" />
          <span className="typing-dot" style={{ animationDelay: "0.15s" }} />
          <span className="typing-dot" style={{ animationDelay: "0.3s" }} />
        </div>
        <p className="mt-1.5 text-xs faint">Working on it. This can take a minute.</p>
      </div>
    </div>
  );
}

function Avatar() {
  return (
    <div
      aria-hidden
      className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
      style={{ border: "1px solid var(--line)", color: "var(--brand-deep)" }}
    >
      <SparkleIcon size={16} />
    </div>
  );
}

function replyTurn(out: Reply, sent: { role: string; content: string }[]): Turn {
  return {
    role: "assistant",
    text: out.reply,
    tools: out.tools,
    citations: out.citations,
    attachments: out.attachments,
    ...(out.approval ? { approval: out.approval, sent } : {}),
  };
}

function Message({
  turn,
  agent,
  onRetry,
  onDecide,
}: {
  turn: Turn;
  agent: Agent;
  onRetry?: () => void;
  onDecide?: (yes: boolean) => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(turn.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be blocked (insecure origin, embedded frame). The
      // text is still selectable, so this is not worth an error message.
    }
  }

  // The person's own message: a soft bubble on the right.
  if (turn.role === "user") {
    return (
      <div className="flex flex-col items-end">
        <div
          className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl px-4 py-2.5 text-base"
          style={{ background: "var(--bubble)" }}
        >
          {turn.text}
        </div>
        {turn.files?.length ? (
          <p className="mt-1 flex items-center gap-1 text-xs faint">
            <PaperclipIcon size={12} /> {turn.files.join(", ")}
          </p>
        ) : null}
      </div>
    );
  }

  if (turn.role === "error") {
    return (
      <div className="flex gap-3">
        <Avatar />
        <div className="min-w-0 flex-1">
          <div className="error whitespace-pre-wrap break-words">
            {/* An agent running as its own app may refuse the portal's sign-in; its message ends with
                the app's address, which should open in a new tab. Only https links are made clickable. */}
            {turn.text.split(/(https:\/\/[^\s<>"']+)/).map((part, i) =>
              i % 2 ? (
                <a key={i} href={part} target="_blank" rel="noopener noreferrer" className="underline">
                  {part}
                </a>
              ) : (
                part
              )
            )}
          </div>
          {onRetry ? (
            <button type="button" className="btn btn-quiet mt-2" onClick={onRetry}>
              <RefreshIcon size={15} /> Try that again
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  // The assistant's reply: plain text on the page, with its mark alongside.
  return (
    <div className="group flex gap-3">
      <Avatar />
      <div className="min-w-0 flex-1">
        <div className="md break-words text-base leading-relaxed">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              // Replies come from agents, so every link leaves the portal in a
              // new tab and gets no access to it.
              a: ({ node, ...p }) => <a {...p} target="_blank" rel="noopener noreferrer" />,
              // Wide tables scroll inside the column instead of stretching it.
              table: ({ node, ...p }) => (
                <div className="md-table">
                  <table {...p} />
                </div>
              ),
            }}
          >
            {turn.text}
          </ReactMarkdown>
        </div>

        {turn.citations?.length ? (
          <div className="mt-3">
            <p className="text-xs faint">Where this came from</p>
            <ul className="mt-1 space-y-0.5">
              {turn.citations.map((c, i) => (
                <li key={i} className="text-sm">
                  {c.url ? (
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="muted underline hover:text-[var(--brand)]"
                    >
                      {c.label}
                    </a>
                  ) : (
                    <span className="muted">{c.label}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {turn.attachments?.length ? (
          <div className="mt-3">
            <p className="text-xs faint">
              {turn.attachments.length > 1 ? "Your files are ready" : "Your file is ready"}
            </p>
            {turn.attachments.map((f, i) =>
              (f.sig || agent.output_volume) && f.path ? (
                <FileLink key={i} endpoint={agent.name} file={f} />
              ) : (
                <div key={i} className="mt-1.5">
                  <code
                    className="block break-all rounded-md px-2 py-1 text-xs"
                    style={{ background: "var(--bubble)" }}
                  >
                    {f.path || f.name}
                  </code>
                  <p className="mt-1 text-xs faint">
                    This file cannot be downloaded from here. It is in Databricks at the path above.
                  </p>
                </div>
              )
            )}
          </div>
        ) : null}

        {turn.approval ? <ApprovalCard approval={turn.approval} decided={turn.decided} onDecide={onDecide} /> : null}

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
          <button
            type="button"
            onClick={copy}
            className="icon-btn !h-8 !w-8 opacity-60 hover:opacity-100 focus:opacity-100"
            title={copied ? "Copied" : "Copy"}
            aria-label="Copy this reply"
          >
            {copied ? <CheckIcon size={16} /> : <CopyIcon size={16} />}
          </button>
          {turn.tools?.length ? (
            <p className="text-xs faint">Worked with: {turn.tools.join(", ")}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** A generated file. Checks the response before saving, so a refusal (not in the
 *  results folder, no READ VOLUME, file not there) is shown here in words rather
 *  than as a raw error page. */
function FileLink({ endpoint, file }: { endpoint: string; file: FileRef }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function get(e: React.MouseEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const res = await fetch(downloadUrl(endpoint, file.path, file.sig));
      if (!res.ok) {
        const text = await res.text();
        let msg = "";
        try {
          const j = JSON.parse(text);
          msg = j.error || j.detail || "";
        } catch {
          msg = text.slice(0, 200);
        }
        throw new Error(msg || `Download failed (${res.status})`);
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = file.name || file.path.split("/").pop() || "download";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (ex: any) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-1.5">
      <a
        href={downloadUrl(endpoint, file.path, file.sig)}
        onClick={get}
        className="flex w-fit items-center gap-2 rounded-xl px-3 py-2 text-sm transition hover:bg-[var(--bubble)]"
        style={{ border: "1px solid var(--line)" }}
        aria-busy={busy}
      >
        ⬇ {busy ? "Downloading…" : `Download ${file.name || file.path}`}
      </a>
      {err ? (
        <p className="mt-1 whitespace-pre-wrap text-xs" style={{ color: "var(--err)" }}>
          {err}
        </p>
      ) : null}
    </div>
  );
}

/** "finalize_media_plan" -> "Finalize media plan". */
function toolLabel(name: string) {
  const words = name.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "A tool";
}

/** The first sentence or two of a tool's own description, whole (never cut mid-sentence). */
function summary(text: string) {
  const flat = text.replace(/\s+/g, " ").trim();
  const parts = flat.match(/[^.!?]+[.!?]+(\s|$)/g) || [flat];
  let out = "";
  for (const p of parts) {
    if (out && (out + p).length > 220) break;
    out += p;
  }
  return out.trim() || flat.slice(0, 220);
}

function argText(v: unknown) {
  if (v === null || v === undefined || v === "") return "(empty)";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** A tool call that changes something, waiting for the person to approve or decline it.
 *  The tool's own description says what it does; what it was given is listed under it. */
function ApprovalCard({
  approval,
  decided,
  onDecide,
}: {
  approval: Approval;
  decided?: "approved" | "declined";
  onDecide?: (yes: boolean) => void;
}) {
  const many = approval.items.length > 1;
  return (
    <div className="card mt-3 p-4" role="group" aria-label="Waiting for your approval">
      <p className="text-sm font-semibold">
        {decided === "approved"
          ? "You approved this"
          : decided === "declined"
            ? "You declined this"
            : many
              ? "The assistant wants to do these. Approve?"
              : "The assistant wants to do this. Approve?"}
      </p>
      <ul className="mt-2 space-y-3">
        {approval.items.map((it) => (
          <ApprovalRow key={it.id} item={it} />
        ))}
      </ul>
      {!decided ? (
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" disabled={!onDecide} onClick={() => onDecide?.(true)}>
            <CheckIcon size={16} /> {many ? "Approve all" : "Approve"}
          </button>
          <button type="button" className="btn btn-quiet" disabled={!onDecide} onClick={() => onDecide?.(false)}>
            Decline
          </button>
        </div>
      ) : null}
      <p className="mt-3 text-xs faint">
        Nothing is done until you decide. Your decision is recorded under your name.
      </p>
    </div>
  );
}

function ApprovalRow({ item }: { item: ApprovalItem }) {
  const args = Object.entries(item.arguments || {});
  const shown = args.slice(0, 8);
  const rest = args.slice(8);
  const rows = (list: [string, unknown][]) => (
    <dl className="mt-1 grid grid-cols-1 gap-x-3 gap-y-0.5 text-xs sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)]">
      {list.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="faint break-words">{k.replace(/_/g, " ")}</dt>
          <dd className="break-words [overflow-wrap:anywhere]">{argText(v).slice(0, 600)}</dd>
        </div>
      ))}
    </dl>
  );
  return (
    <li className="min-w-0">
      <p className="text-[15px] font-medium break-words">{toolLabel(item.tool)}</p>
      {item.description ? <p className="mt-0.5 text-sm muted break-words">{summary(item.description)}</p> : null}
      {shown.length ? rows(shown) : null}
      {rest.length ? (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs faint">{rest.length} more</summary>
          {rows(rest)}
        </details>
      ) : null}
    </li>
  );
}
