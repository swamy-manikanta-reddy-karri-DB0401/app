"use client";

import { useEffect, useRef, useState } from "react";
import { Agent, api, CustomSpec, CustomSummary, BuilderTool, DesignerInfo, FileSettings, McpEntry, SourceItem, ToolType } from "@/lib/api";
import { ErrorBox, Select, Spinner, Tag, useLoad } from "./bits";
import { ArrowRightIcon, CheckIcon, PlusIcon, SparkleIcon } from "./icons";
import { Access, AccessStep, Done, Finished, Section, VolumeField, WizardFrame } from "./BuilderParts";
import { KnowledgeWizard } from "./KnowledgeBuilder";
import { GenieWizard } from "./GenieBuilder";
import { Designer } from "./Designer";
import { AppAgentWizard } from "./AppAgentBuilder";
import { BuildLibrary, LibGroup, LibItem } from "./BuildLibrary";
import { nameOf } from "@/lib/people";
import { CustomWizard } from "./custom/CustomWizard";
import { CustomAgentPage } from "./custom/CustomAgentPage";
import { ChosenRow, isCatalogTool, McpPicker, ToolProgress, ToolWait } from "./McpCatalog";

type Row = Awaited<ReturnType<typeof api.builderAgents>>["agents"][number];

/** Plain-language names for the technical tool types. The exact technical name is
 *  always shown small next to what the person picked, so nothing is hidden from
 *  an admin who knows Databricks - it is just not what they have to read first. */
const ABILITY: Record<string, { title: string; blurb: string; noun: string }> = {
  uc_function: {
    title: "Run a function",
    blurb: "A saved calculation or lookup, such as “get a weather forecast”.",
    noun: "function",
  },
  genie_space: {
    title: "Answer questions from data",
    blurb: "Lets it answer questions about your tables in plain English (a Genie space).",
    noun: "data space",
  },
  knowledge_assistant: {
    title: "Ask a document expert",
    blurb: "Hands questions to another assistant that knows your documents.",
    noun: "document expert",
  },
  vector_search_index: {
    title: "Search documents",
    blurb: "Looks things up in a collection of documents that has been prepared for searching.",
    noun: "document search",
  },
  volume: {
    title: "Read files from a folder",
    blurb: "Lets it open files that are stored in a folder.",
    noun: "folder",
  },
  uc_connection: {
    title: "Use an outside tool",
    blurb: "Connects to another system through an MCP connection.",
    noun: "outside tool",
  },
  app: {
    title: "Use a connected app",
    blurb: "Uses a Databricks app that offers tools (an MCP app).",
    noun: "app",
  },
};

// Kinds whose reference is "catalog.schema.name", so the picker browses to them.
const BROWSE = new Set(["uc_function", "volume"]);

const ALL_STEPS = [
  { key: "basics", title: "Name it" },
  { key: "behave", title: "Instructions" },
  { key: "abilities", title: "Abilities" },
  { key: "files", title: "Files" },
  { key: "access", title: "Who can use it" },
  { key: "review", title: "Review" },
] as const;

type StepKey = (typeof ALL_STEPS)[number]["key"];

const FILE_MARKER = "File handling:";

/** Thrown to stop waiting for tools when the person leaves the wizard. */
class Cancelled extends Error {}

const EXAMPLE_PROMPT = [
  "You are a helpful assistant for our team. Your job is to ______.",
  "",
  "How to work:",
  "- Use the tools you have been given to find the answer rather than guessing.",
  "- If you are not sure what the person means, ask one short question first.",
  "- Keep answers short and in plain language. Use a list when there are several points.",
  "- If you cannot find the answer, say so honestly.",
].join("\n");

function fileInstructions() {
  return [
    FILE_MARKER,
    '- When the user message says "Uploaded file: <path>", that path is the input file. Pass it exactly as written as the input path of the tool.',
    '- When it says "Save any generated output file to this directory: <dir>", pass that directory as the output path of the tool.',
    "- After the tool succeeds, reply with the full /Volumes/... path of the file it produced, so the user can download it.",
  ].join("\n");
}

const FILE_TYPES = [
  { ext: "xlsx", label: "Excel" },
  { ext: "csv", label: "CSV" },
  { ext: "pdf", label: "PDF" },
  { ext: "docx", label: "Word" },
  { ext: "pptx", label: "PowerPoint" },
  { ext: "txt", label: "Text" },
];

function parseTypes(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim().replace(/^\./, "").toLowerCase())
    .filter(Boolean);
}

// ---------------------------------------------------------------------------

export function Builder({ onGoto, onTry, agents }: { onGoto?: (tab: string) => void; onTry?: (key: string) => void; agents?: Agent[] }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [spaces, setSpaces] = useState<{ space_id: string; title: string; description: string }[] | null>(null);
  const [err, setErr] = useState("");
  const [spacesErr, setSpacesErr] = useState("");
  const [kas, setKas] = useState<{ ka_id: string; display_name: string; description: string; state: string; endpoint_name?: string }[] | null>(null);
  const [kasErr, setKasErr] = useState("");
  // What is on screen: the lists, the "what kind?" picker, or one of the wizards.
  const [view, setView] = useState<
    | { v: "home" }
    | { v: "pick" }
    | { v: "assistant"; id: string | null }
    | { v: "genie"; id: string | null }
    | { v: "docs"; id: string | null }
    | { v: "designer" }
    | { v: "app"; agent: Agent | null }
    | { v: "custom"; id: string | null; initial: CustomSpec | null }
    | { v: "customPage"; id: string; warnings?: string[] }
  >({ v: "home" });
  const [customs, setCustoms] = useState<{ enabled: boolean; reason?: string; agents: CustomSummary[] } | null>(null);
  const [customsErr, setCustomsErr] = useState("");
  const [apps, setApps] = useState<{ enabled: boolean; reason?: string; agents: Agent[] } | null>(null);
  const [appsErr, setAppsErr] = useState("");
  // The "describe it in your own words" beta: offered unless an operator switched it off. When it is
  // on but this admin has no AI model to use, it is shown with the reason, not hidden.
  const [designer, setDesigner] = useState<DesignerInfo | null>(null);
  const [done, setDone] = useState<Finished | null>(null);

  async function load() {
    setErr("");
    setSpacesErr("");
    setKasErr("");
    try {
      setRows((await api.builderAgents()).agents);
    } catch (e: any) {
      setRows([]);
      setErr(e.message);
    }
    try {
      setSpaces((await api.genieList()).spaces);
    } catch (e: any) {
      setSpaces([]);
      setSpacesErr(e.message);
    }
    try {
      setKas((await api.knowledgeList()).assistants);
    } catch (e: any) {
      setKas([]);
      setKasErr(e.message);
    }
    setCustomsErr("");
    try {
      setCustoms(await api.customAgents());
    } catch (e: any) {
      setCustoms({ enabled: false, agents: [] });
      setCustomsErr(e.message);
    }
    setAppsErr("");
    try {
      setApps(await api.appAgents());
    } catch (e: any) {
      setApps({ enabled: false, agents: [] });
      setAppsErr(e.message);
    }
  }
  useEffect(() => {
    load();
    api
      .designerStatus()
      .then(setDesigner)
      .catch(() => setDesigner(null));
  }, []);

  const finished = (d: Finished) => {
    setDone(d);
    setView({ v: "home" });
    load();
  };

  if (view.v === "assistant") {
    return <Wizard id={view.id} onCancel={() => setView({ v: "home" })} onFinished={finished} />;
  }
  if (view.v === "docs") {
    return <KnowledgeWizard id={view.id} onCancel={() => setView({ v: "home" })} onFinished={finished} />;
  }
  if (view.v === "genie") {
    return <GenieWizard id={view.id} onCancel={() => setView({ v: "home" })} onFinished={finished} />;
  }
  if (view.v === "custom") {
    return (
      <CustomWizard
        id={view.id}
        initial={view.initial}
        onCancel={() => setView(view.id ? { v: "customPage", id: view.id } : { v: "home" })}
        onSaved={(id, warnings) => {
          load();
          setView({ v: "customPage", id, warnings });
        }}
      />
    );
  }
  if (view.v === "customPage") {
    return (
      <CustomAgentPage
        id={view.id}
        onBack={() => {
          load();
          setView({ v: "home" });
        }}
        onEdit={(d) => setView({ v: "custom", id: d.id, initial: d.spec })}
        onTry={(k) => onTry?.(k)}
      />
    );
  }
  if (view.v === "app") {
    return <AppAgentWizard editing={view.agent} onCancel={() => setView({ v: "home" })} onFinished={finished} />;
  }
  if (view.v === "designer") {
    return <Designer onCancel={() => setView({ v: "home" })} onFinished={finished} />;
  }

  if (view.v === "pick") {
    return (
      <div className="w-full">
        <div className="mb-5 flex items-center gap-3">
          <button type="button" className="btn btn-quiet" onClick={() => setView({ v: "home" })}>
            ← Cancel
          </button>
          <h2 className="text-xl font-semibold">What kind of assistant do you want to create?</h2>
        </div>
        {designer?.enabled ? (
          <>
            <section
              className="mb-6 overflow-hidden rounded-2xl"
              style={{
                background: "radial-gradient(120% 160% at 0% 0%, var(--brand-soft) 0%, var(--surface) 60%)",
                border: "1px solid color-mix(in srgb, var(--brand) 45%, var(--line))",
                boxShadow: "var(--shadow-lg)",
              }}
              aria-label="Describe it in your own words"
            >
              <div className="flex flex-wrap items-center gap-6 p-6 sm:p-7">
                <span aria-hidden className="dz-mark dz-mark-lg">
                  <SparkleIcon size={26} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-[19px] font-semibold tracking-tight">Not sure which to choose? Just describe it.</h3>
                    <Tag title="A new way to build that is still being tested. Everything is shown to you before it is created.">Beta</Tag>
                  </div>
                  <p className="mt-1 max-w-3xl text-[15px] muted">
                    Say what you want in your own words. A short conversation works out the right kind of assistant, checks what your
                    workspace already has, and shows you everything before anything is created.
                  </p>
                  <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px] muted">
                    <li className="flex items-center gap-1.5">
                      <span style={{ color: "var(--ok)" }} aria-hidden>
                        <CheckIcon size={14} />
                      </span>
                      Picks the right kind for you
                    </li>
                    <li className="flex items-center gap-1.5">
                      <span style={{ color: "var(--ok)" }} aria-hidden>
                        <CheckIcon size={14} />
                      </span>
                      Reuses the tools and data you have
                    </li>
                    <li className="flex items-center gap-1.5">
                      <span style={{ color: "var(--ok)" }} aria-hidden>
                        <CheckIcon size={14} />
                      </span>
                      Tests it before you share it
                    </li>
                  </ul>
                </div>
                <div className="flex w-full flex-col items-start gap-2 sm:w-auto sm:items-end">
                  <button type="button" className="btn btn-primary btn-lg" disabled={!designer.ready} onClick={() => setView({ v: "designer" })}>
                    Describe your assistant <ArrowRightIcon size={16} />
                  </button>
                  {!designer.ready ? <p className="max-w-xs text-sm muted">{designer.reason}</p> : null}
                </div>
              </div>
            </section>
            <div className="mb-4 flex items-center gap-3 text-sm muted">
              <span>Or choose the kind yourself</span>
              <span aria-hidden className="h-px flex-1" style={{ background: "var(--line)" }} />
            </div>
          </>
        ) : null}
        <div className="grid grid-cols-[repeat(auto-fill,minmax(min(320px,100%),1fr))] gap-4">
          <button
            type="button"
            className="choice !p-6"
            aria-pressed={false}
            disabled={customs !== null && !customs.enabled}
            onClick={() => setView({ v: "custom", id: null, initial: null })}
          >
            <span>
              <span className="flex flex-wrap items-center gap-2 text-lg font-semibold">
                Custom agent <Tag title="Tested and approved before anyone else can use it">Test and release</Tag>
              </span>
              <span className="mt-1 block text-[15px] muted">
                You choose its AI model and what it can use: documents, tools, data and other assistants. It is tested, then approved
                by people you choose, before anyone else can use it.
              </span>
              <span className="mt-3 block text-[13px] faint">
                {customs !== null && !customs.enabled ? customs.reason : "Good for: agents that need a review before release"}
              </span>
            </span>
          </button>
          <button type="button" className="choice !p-6" aria-pressed={false} onClick={() => setView({ v: "assistant", id: null })}>
            <span>
              <span className="block text-lg font-semibold">Combine tools and information</span>
              <span className="mt-1 block text-[15px] muted">
                The all-rounder. It can run functions, answer questions from data, ask a document expert, read
                files and use outside tools, and it decides which to use for each question.
              </span>
              <span className="mt-3 block text-[13px] faint">Good for: most things</span>
            </span>
          </button>
          <button type="button" className="choice !p-6" aria-pressed={false} onClick={() => setView({ v: "docs", id: null })}>
            <span>
              <span className="block text-lg font-semibold">Answer questions from your documents</span>
              <span className="mt-1 block text-[15px] muted">
                Point it at folders of PDFs, Word files and presentations. People ask questions and get answers
                taken from those documents.
              </span>
              <span className="mt-3 block text-[13px] faint">Good for: policies, manuals, contracts and reports</span>
            </span>
          </button>
          <button type="button" className="choice !p-6" aria-pressed={false} onClick={() => setView({ v: "genie", id: null })}>
            <span>
              <span className="block text-lg font-semibold">Answer questions from your data</span>
              <span className="mt-1 block text-[15px] muted">
                People ask questions about your tables in plain English and get answers back. You choose the tables
                and give it a few example questions.
              </span>
              <span className="mt-3 block text-[13px] faint">Good for: sales, finance and operations numbers</span>
            </span>
          </button>
          <button
            type="button"
            className="choice !p-6"
            aria-pressed={false}
            disabled={apps !== null && !apps.enabled}
            onClick={() => setView({ v: "app", agent: null })}
          >
            <span>
              <span className="block text-lg font-semibold">Add an agent built in code</span>
              <span className="mt-1 block text-[15px] muted">
                Your developers wrote an agent and run it as an app in Databricks. Add it here so people can chat with it
                like any other assistant.
              </span>
              <span className="mt-3 block text-[13px] faint">
                {apps !== null && !apps.enabled ? apps.reason : "Good for: agents your team already built in code"}
              </span>
            </span>
          </button>
        </div>
      </div>
    );
  }

  if (done) {
    return (
      <Done
        done={done}
        onGoto={onGoto}
        onBack={() => setDone(null)}
        onNew={() => {
          setDone(null);
          setView({ v: "pick" });
        }}
      />
    );
  }

  // Is it up, and can it be tried? Read from the assistant list the page already has; nothing is
  // said about an assistant that is not in it.
  const byEndpoint = new Map((agents || []).map((a) => [a.name, a]));
  const live = (endpoint: string): Pick<LibItem, "status" | "tryKey"> => {
    const a = byEndpoint.get(endpoint);
    if (!a) return {};
    return a.ready ? { status: { label: "Ready", tone: "ok" }, tryKey: a.name } : { status: { label: "Starting up", tone: "off" } };
  };
  // Only a person is named as owner: a service identity's id means nothing to a reader.
  const person = (who?: string): [string, string][] => (who && who.includes("@") ? [["Owner", nameOf(who)]] : []);
  const NO_DESC = "Add a description so people know what it is for.";

  const RELEASE: Record<string, LibItem["status"]> = {
    live: { label: "Live", tone: "ok" },
    waiting: { label: "Waiting for approval", tone: "wait" },
    draft: { label: "Draft", tone: "off" },
    retired: { label: "Retired", tone: "off" },
  };
  const groups: LibGroup[] = [
    {
      kind: "custom",
      items: customs === null ? null : customs.agents.map((c) => {
        const st = c.latest.status;
        const sub = c.latest.meta?.submitted;
        const note =
          st === "waiting"
            ? `Waiting for ${(sub?.approvers || []).join(", ") || "an approver"} to approve it.`
            : st !== "draft"
              ? undefined
              : c.ready_to_send
                ? "Tested and ready. Send it for approval."
                : !c.latest.meta?.test
                  ? "Not tested yet. Run its tests, then send it for approval."
                  : c.next || "Some test answers need a look.";
        const facts: [string, string][] = [["Version", `v${c.latest.version}`]];
        if (c.live_version) facts.push(["Live version", `v${c.live_version}`]);
        facts.push(...person(c.latest.created_by));
        return {
          key: c.id,
          kind: "custom" as const,
          title: c.name,
          description: c.description,
          status: c.live_version && st !== "live" ? { label: "Live", tone: "ok" as const } : RELEASE[st] || { label: c.latest.status_label, tone: "off" as const },
          facts,
          note,
          tryKey: c.live_version ? "ca:" + c.id : undefined,
          manageLabel: st === "draft" ? "Test and release" : "Manage",
          onOpen: () => setView({ v: "customPage", id: c.id }),
        };
      }),
      error: customsErr,
      off: customs && !customs.enabled ? customs.reason : undefined,
    },
    {
      kind: "assistant",
      items: rows === null ? null : rows.map((r) => ({
        key: r.agent_id,
        kind: "assistant" as const,
        title: r.display_name,
        description: r.description,
        ...live(r.endpoint_name),
        facts: person(r.creator),
        note: r.description?.trim() ? undefined : NO_DESC,
        manageLabel: "Change settings",
        onOpen: () => setView({ v: "assistant", id: r.agent_id }),
      })),
      error: err,
    },
    {
      kind: "docs",
      items: kas === null ? null : kas.map((k) => ({
        key: k.ka_id,
        kind: "docs" as const,
        title: k.display_name,
        description: k.description,
        ...live(k.endpoint_name || ""),
        note: k.description?.trim() ? undefined : NO_DESC,
        manageLabel: "Change settings",
        onOpen: () => setView({ v: "docs", id: k.ka_id }),
      })),
      error: kasErr,
    },
    {
      kind: "data",
      items: spaces === null ? null : spaces.map((sp) => {
        // Genie spaces an app creates for itself ("... (auto-created) <date>") crowd out the ones people made.
        const auto = /\(auto-created\)/i.test(sp.title || "");
        return {
          key: sp.space_id,
          kind: "data" as const,
          title: sp.title,
          description: sp.description,
          auto,
          note: auto || sp.description?.trim() ? undefined : NO_DESC,
          manageLabel: "Change settings",
          onOpen: () => setView({ v: "genie", id: sp.space_id }),
        };
      }),
      error: spacesErr,
    },
    {
      kind: "code",
      items: apps === null ? null : apps.agents.map((a) => ({
        key: a.name,
        kind: "code" as const,
        title: a.display_name,
        description: a.blurb || "",
        status: a.ready ? { label: "Running", tone: "ok" as const } : { label: "Not running", tone: "warn" as const },
        facts: [["App", a.app_name || ""]] as [string, string][],
        note: a.ready ? undefined : "Its app is not running, so nobody can use it. Start it in Databricks.",
        tryKey: a.ready ? a.name : undefined,
        manageLabel: "Change settings",
        onOpen: () => setView({ v: "app", agent: a }),
      })),
      error: appsErr,
      off: apps && !apps.enabled ? apps.reason : undefined,
    },
  ];

  return <BuildLibrary groups={groups} onCreate={() => setView({ v: "pick" })} onTry={onTry} />;
}

// ---------------------------------------------------------------------------

function Wizard({
  id,
  onCancel,
  onFinished,
}: {
  id: string | null;
  onCancel: () => void;
  onFinished: (d: Finished) => void;
}) {
  const editing = !!id;
  // Access is only chosen when creating. For an existing assistant it lives on
  // People & access, where the current list can be seen and changed.
  const steps = editing ? ALL_STEPS.filter((x) => x.key !== "access") : ALL_STEPS;
  const keys: StepKey[] = steps.map((x) => x.key);
  const last = steps.length - 1;

  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(0);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [tools, setTools] = useState<BuilderTool[]>([]);
  const [files, setFiles] = useState<FileSettings>({ upload_volume: "", output_volume: "", accepts: "" });
  const [wantUpload, setWantUpload] = useState(false);
  const [wantOutput, setWantOutput] = useState(false);
  const [teach, setTeach] = useState(true);
  const [access, setAccess] = useState<Access[]>([]);
  const [loading, setLoading] = useState(!!id);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // What the person sees under the button while their tools are got ready.
  const [wait, setWait] = useState<{ items: ToolWait[]; since: number; creating: boolean; failed: boolean } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false; // leaving the wizard stops the waiting
    };
  }, []);
  useEffect(() => {
    if (!wait || wait.failed) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [wait]);
  const [showPrompt, setShowPrompt] = useState(false);
  // Ready-made tools from the team's git repository, with what is already deployed.
  const [catalog, setCatalog] = useState<McpEntry[] | null>(null);
  const [catalogErr, setCatalogErr] = useState("");
  const [catalogNote, setCatalogNote] = useState("");

  useEffect(() => {
    api
      .mcps()
      .then((d) => {
        setCatalog(d.mcps);
        setCatalogNote(d.note);
      })
      .catch((e) => {
        setCatalog([]);
        setCatalogErr(e.message);
      });
  }, []);

  useEffect(() => {
    if (!id) return;
    api
      .builderGet(id)
      .then((a) => {
        setName(a.display_name);
        setDescription(a.description);
        setInstructions(a.instructions);
        setTools(a.tools);
        setFiles(a.files);
        setWantUpload(!!a.files.upload_volume);
        setWantOutput(!!a.files.output_volume);
        // An agent that already carries the file block should not get it twice.
        setTeach(!a.instructions.includes(FILE_MARKER));
        setReached(last);
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const cur = keys[step];
  const editable = tools.filter((t) => !t.readonly);
  const usesFiles = !!(files.upload_volume || files.output_volume);
  const finalInstructions =
    usesFiles && teach && !instructions.includes(FILE_MARKER)
      ? instructions.trim()
        ? instructions.trimEnd() + "\n\n" + fileInstructions()
        : fileInstructions()
      : instructions;

  const problem =
    cur === "basics" && !name.trim()
      ? "Give your assistant a name to continue."
      : cur === "files" && wantUpload && !files.upload_volume
        ? "Choose the folder where uploaded files should go, or switch uploads off."
        : cur === "files" && wantOutput && !files.output_volume
          ? "Choose the folder where results should be saved, or switch results off."
          : "";

  function go(i: number) {
    setErr("");
    setStep(i);
    setReached((r) => Math.max(r, i));
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }
  const goTo = (k: StepKey) => go(Math.max(0, keys.indexOf(k)));

  /** The assistant is only made once every tool it needs is ready, so nobody ends up
   *  with an assistant that cannot do what they picked. Tools that are not there yet
   *  are got ready first, which can take a few minutes; the panel under the button
   *  shows where each one is up to. */
  async function readyTools(slugs: string[]) {
    if (!slugs.length) return;
    const mineOf = (d: { mcps: McpEntry[] }) => d.mcps.filter((m) => slugs.includes(m.slug));
    const show = (mine: McpEntry[], failed = false) =>
      setWait((w) => ({
        since: w?.since ?? Date.now(),
        creating: false,
        failed,
        items: mine.map((m) => ({ slug: m.slug, name: m.name, state: m.state, line: m.state_note })),
      }));
    let d = await api.mcps(true);
    if (mineOf(d).every((m) => m.state === "running")) return;
    show(mineOf(d));
    await api.mcpPrepare(slugs);
    show(mineOf(await api.mcps()));
    const started = Date.now();
    let kicked = started;
    for (;;) {
      await new Promise((r) => setTimeout(r, 6000));
      if (!alive.current) throw new Cancelled();
      d = await api.mcps();
      const mine = mineOf(d);
      const bad = mine.find((m) => m.state === "failed");
      show(mine, !!bad);
      if (bad) {
        throw new Error(
          `We could not get “${bad.name}” ready, so nothing was created. You can try again, or untick that tool.`
        );
      }
      if (mine.every((m) => m.state === "running")) return;
      // Waiting, but nothing is working on it (for example the server restarted
      // part-way): ask for it again. Asking twice is harmless.
      const idle = mine.filter((m) => m.state === "not_deployed" || m.state === "stopped");
      if (idle.length && Date.now() - kicked > 30000) {
        kicked = Date.now();
        await api.mcpPrepare(idle.map((m) => m.slug));
      }
      if (Date.now() - started > 20 * 60 * 1000) {
        throw new Error("Getting the tools ready is taking longer than expected. Nothing was created. Please try again in a few minutes.");
      }
    }
  }

  async function save() {
    setBusy(true);
    setErr("");
    setWait(null);
    try {
      await readyTools(Array.from(new Set(editable.filter((t) => t.mcp).map((t) => t.mcp as string))));
    } catch (e: any) {
      if (e instanceof Cancelled || !alive.current) return;
      setErr(e.message);
      setBusy(false);
      return;
    }
    setWait((w) => (w ? { ...w, creating: true } : w));
    const spec = {
      display_name: name,
      description,
      instructions: finalInstructions,
      tools: editable.map((t) => ({ type: t.type, ref: t.ref, description: t.description, mcp: t.mcp })),
      files,
      access,
    };
    try {
      if (id) {
        const r = await api.builderUpdate(id, spec);
        onFinished({ kind: "saved", name, warnings: r.warnings, deploying: r.deploying });
      } else {
        const r = await api.builderCreate(spec);
        onFinished({
          kind: "created",
          name,
          warnings: r.warnings,
          by: r.acted_as,
          access: r.access_pending,
          deploying: r.deploying,
        });
      }
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  }

  async function remove() {
    if (!id) return;
    setBusy(true);
    setErr("");
    try {
      await api.builderDelete(id);
      onFinished({ kind: "deleted", name });
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  }

  if (loading) return <Spinner label="Loading…" />;

  return (
    <WizardFrame
      title={editing ? "Change an assistant" : "Create an assistant"}
      steps={steps.map((x) => ({ key: x.key, title: x.title }))}
      step={step}
      reached={reached}
      editing={editing}
      onGo={go}
      err={err}
      problem={problem}
      busy={busy}
      status={
        wait ? (
          <ToolProgress
            items={wait.items}
            seconds={Math.max(0, Math.floor((now - wait.since) / 1000))}
            creating={wait.creating}
            failed={wait.failed}
          />
        ) : undefined
      }
      finishLabel={editing ? "Save changes" : "Create assistant"}
      finishDisabled={!name.trim()}
      onFinish={save}
      onCancel={onCancel}
      onDelete={editing ? remove : undefined}
      deleteLabel="Delete this assistant"
    >
      {cur === "basics" ? (
        <Basics name={name} description={description} setName={setName} setDescription={setDescription} />
      ) : null}

      {cur === "behave" ? <Behave instructions={instructions} setInstructions={setInstructions} /> : null}

      {cur === "abilities" ? (
        <Abilities
          tools={tools}
          setTools={setTools}
          catalog={catalog}
          catalogErr={catalogErr}
          catalogNote={catalogNote}
        />
      ) : null}

      {cur === "files" ? (
        <FilesStep
          files={files}
          setFiles={setFiles}
          wantUpload={wantUpload}
          wantOutput={wantOutput}
          setWantUpload={setWantUpload}
          setWantOutput={setWantOutput}
          teach={teach}
          setTeach={setTeach}
          alreadyTaught={instructions.includes(FILE_MARKER)}
        />
      ) : null}

      {cur === "access" ? <AccessStep access={access} setAccess={setAccess} /> : null}

      {cur === "review" ? (
        <Review
          name={name}
          description={description}
          instructions={finalInstructions}
          tools={tools}
          catalog={catalog}
          files={files}
          access={editing ? null : access}
          showPrompt={showPrompt}
          setShowPrompt={setShowPrompt}
          go={goTo}
        />
      ) : null}
    </WizardFrame>
  );
}

// --------------------------------------------------------------- step 1 -----

function Basics({
  name,
  description,
  setName,
  setDescription,
}: {
  name: string;
  description: string;
  setName: (v: string) => void;
  setDescription: (v: string) => void;
}) {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">What do you want to call it?</h3>
        <p className="mt-1 text-[15px] muted">This is the name people will see in their list.</p>
      </div>
      <label className="block">
        <span className="label">Name</span>
        <input
          className="field mt-2"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          placeholder="For example: Weekly Sales Helper"
          autoFocus
        />
        <span className="help">It must be different from every other assistant in your workspace.</span>
      </label>
      <label className="block">
        <span className="label">What is it for? (one or two sentences)</span>
        <textarea
          className="field mt-2 min-h-[96px]"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="For example: Answers questions about weekly sales and turns a spreadsheet into a slide deck."
        />
        <span className="help">Shown on the assistant&rsquo;s card so people know when to pick it.</span>
      </label>
    </div>
  );
}

// --------------------------------------------------------------- step 2 -----

function Behave({
  instructions,
  setInstructions,
}: {
  instructions: string;
  setInstructions: (v: string) => void;
}) {
  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-lg font-semibold">How should it behave?</h3>
        <p className="mt-1 text-[15px] muted">
          Think of this as a briefing for a new colleague. You can skip it for now and come back later.
        </p>
      </div>
      <ul className="list-disc space-y-1 pl-5 text-[15px] muted">
        <li>Who is it helping, and what is its job?</li>
        <li>Which tool should it use, and when?</li>
        <li>How should it word its answers: short, formal, friendly?</li>
        <li>What should it never do?</li>
      </ul>
      <label className="block">
        <span className="label">Instructions</span>
        <textarea
          className="field mt-2 min-h-[260px]"
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="Write your instructions here, in plain sentences."
        />
      </label>
      {!instructions.trim() ? (
        <button type="button" className="btn btn-quiet" onClick={() => setInstructions(EXAMPLE_PROMPT)}>
          Start from an example
        </button>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------- step 3 -----

function Abilities({
  tools,
  setTools,
  catalog,
  catalogErr,
  catalogNote,
}: {
  tools: BuilderTool[];
  setTools: (t: BuilderTool[]) => void;
  catalog: McpEntry[] | null;
  catalogErr: string;
  catalogNote: string;
}) {
  const [adding, setAdding] = useState(false);
  // Catalog tools are shown (and changed) in the picker above, not repeated here.
  const others = tools.filter((t) => !isCatalogTool(t, catalog));
  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-lg font-semibold">What should it be able to do?</h3>
        <p className="mt-1 text-[15px] muted">
          Give it the tools and information it can use. You can skip this and add them later.
        </p>
      </div>

      <McpPicker catalog={catalog} err={catalogErr} note={catalogNote} tools={tools} setTools={setTools} />

      {/* Abilities that are not ready-made tools: functions, data, documents and
          connections in Databricks. Same card and rows as the picker above. */}
      <section className="card min-w-0 overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
          <div className="min-w-0">
            <h4 className="text-[15px] font-semibold tracking-[-0.01em]">Other abilities</h4>
            <p className="mt-0.5 text-[13px] faint">Functions, data, documents and connections already in Databricks.</p>
          </div>
          {!adding ? (
            <button
              type="button"
              className="btn btn-quiet !min-h-[34px] !text-[13px]"
              onClick={() => setAdding(true)}
            >
              <PlusIcon size={15} />
              Add an ability
            </button>
          ) : null}
        </div>
        {others.length === 0 && !adding ? (
          <p className="border-t px-5 py-4 text-[13px] faint" style={{ borderColor: "var(--line)" }}>
            None added.
          </p>
        ) : null}
        {others.length ? (
          <ul>
            {others.map((t) => {
              const a = ABILITY[t.type];
              return (
                <ChosenRow
                  key={t.type + t.ref}
                  title={a?.title || t.type.replace(/_/g, " ")}
                  sub={t.ref}
                  value={t.description}
                  placeholder="For example: when someone asks about the weather"
                  onChange={t.readonly ? undefined : (v) => setTools(tools.map((x) => (x === t ? { ...x, description: v } : x)))}
                  onRemove={t.readonly ? undefined : () => setTools(tools.filter((x) => x !== t))}
                  locked={t.readonly ? "Added in Databricks. Left as it is." : undefined}
                />
              );
            })}
          </ul>
        ) : null}
        {adding ? (
          <div className="border-t p-5" style={{ borderColor: "var(--line)" }}>
            <AddAbility
              have={tools}
              onAdd={(t) => {
                setTools([...tools, t]);
                setAdding(false);
              }}
              onCancel={() => setAdding(false)}
            />
          </div>
        ) : null}
      </section>
    </div>
  );
}

function AddAbility({
  have,
  onAdd,
  onCancel,
}: {
  have: BuilderTool[];
  onAdd: (t: BuilderTool) => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState("");
  const [catalog, setCatalog] = useState("");
  const [schema, setSchema] = useState("");
  const [pick, setPick] = useState("");
  const [typed, setTyped] = useState("");
  const [desc, setDesc] = useState("");
  const browse = BROWSE.has(kind);

  // Each list says whether it is still loading, so its dropdown can show it.
  const typesL = useLoad(() => api.builderTypes(), []);
  const catsL = useLoad(kind && browse ? () => api.builderSources("catalogs") : null, [kind]);
  const schsL = useLoad(browse && catalog ? () => api.builderSources("schemas", catalog) : null, [kind, catalog]);
  const itemsL = useLoad(
    !kind ? null : !browse ? () => api.builderSources(kind) : catalog && schema ? () => api.builderSources(kind, catalog, schema) : null,
    [kind, catalog, schema],
  );
  const types: ToolType[] = typesL.data?.types || [];
  const catalogs = catsL.data?.items || [];
  const schemas = schsL.data?.items || [];
  const items = itemsL.data?.items || [];
  const note = itemsL.error || itemsL.data?.note || catsL.error || catsL.data?.note || "";

  // Reset the choices below whatever changed.
  useEffect(() => {
    setCatalog("");
    setSchema("");
    setPick("");
    setTyped("");
  }, [kind]);
  useEffect(() => {
    setSchema("");
    setPick("");
  }, [catalog]);
  useEffect(() => setPick(""), [schema]);

  const ref = (typed || pick).trim();
  const added = (r: string) => have.some((t) => t.type === kind && t.ref === r);
  const taken = added(ref);
  const current = types.find((t) => t.type === kind);
  const a = ABILITY[kind];

  function add() {
    if (!ref || taken) return;
    onAdd({
      type: kind,
      ref,
      description: desc.trim() || items.find((i) => i.value === ref)?.detail || "",
    });
  }

  return (
    <div className="rounded-xl p-5" style={{ border: "1px solid var(--brand)", background: "var(--canvas)" }}>
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-[15px] font-semibold">
          {kind ? a?.title || "Add an ability" : "What kind of ability?"}
        </h4>
        <button type="button" className="btn btn-quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>

      {!kind && typesL.loading ? (
        <div className="mt-4 grid gap-3 sm:grid-cols-2" aria-busy>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="card h-[74px] animate-pulse" />
          ))}
        </div>
      ) : !kind && typesL.error ? (
        <div className="mt-4">
          <ErrorBox>Could not load the kinds of ability ({typesL.error}).</ErrorBox>
        </div>
      ) : !kind ? (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {types.map((t) => {
            const info = ABILITY[t.type];
            return (
              <button key={t.type} type="button" className="choice" aria-pressed={false} onClick={() => setKind(t.type)}>
                <span>
                  <span className="block text-[15px] font-semibold">{info?.title || t.label}</span>
                  <span className="mt-0.5 block text-[13px] muted">{info?.blurb || t.label}</span>
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          <p className="text-[15px] muted">{a?.blurb}</p>

          {BROWSE.has(kind) ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-[13px] font-medium muted">1. Catalog (the top-level area)</span>
                <Select
                  className="mt-1"
                  value={catalog}
                  onChange={setCatalog}
                  options={catalogs}
                  loading={catsL.loading}
                />
              </label>
              <label className="block">
                <span className="text-[13px] font-medium muted">2. Schema (the group inside it)</span>
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
            </div>
          ) : null}

          <label className="block">
            <span className="text-[13px] font-medium muted">
              {BROWSE.has(kind) ? `3. Choose the ${a?.noun}` : `Choose the ${a?.noun}`}
            </span>
            <Select
              className="mt-1"
              value={pick}
              onChange={(v) => {
                setPick(v);
                setTyped("");
              }}
              options={items.map((i) => (added(i.value) ? { ...i, disabled: true, note: "Added" } : i))}
              loading={itemsL.loading}
              disabled={browse && !(catalog && schema)}
              placeholder={browse && !(catalog && schema) ? "Pick a catalog and schema first" : "Choose one…"}
            />
          </label>

          {note ? <p className="help">{note}</p> : null}

          <details>
            <summary className="cursor-pointer text-[13px] font-medium muted">
              Advanced: type the exact name instead
            </summary>
            <input
              className="field mt-2"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={
                BROWSE.has(kind) || kind === "vector_search_index"
                  ? "catalog.schema.name"
                  : "The exact name or id"
              }
            />
          </details>

          <label className="block">
            <span className="text-[13px] font-medium muted">When should it use this? (optional)</span>
            <input
              className="field mt-1"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="For example: when someone asks about sales"
            />
          </label>

          {current && !current.confirmed ? (
            <p className="help">
              Databricks has not published the exact setup for this kind of ability, so it might be
              rejected. If it is, nothing is created and you will be told why.
            </p>
          ) : null}
          {taken ? <p className="help">You have already added that one.</p> : null}

          <div className="flex flex-wrap gap-3">
            <button type="button" className="btn btn-primary" onClick={add} disabled={!ref || taken}>
              Add this ability
            </button>
            <button type="button" className="btn btn-quiet" onClick={() => setKind("")}>
              Choose a different kind
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------- step 4 -----

function FilesStep({
  files,
  setFiles,
  wantUpload,
  wantOutput,
  setWantUpload,
  setWantOutput,
  teach,
  setTeach,
  alreadyTaught,
}: {
  files: FileSettings;
  setFiles: (f: FileSettings) => void;
  wantUpload: boolean;
  wantOutput: boolean;
  setWantUpload: (v: boolean) => void;
  setWantOutput: (v: boolean) => void;
  teach: boolean;
  setTeach: (v: boolean) => void;
  alreadyTaught: boolean;
}) {
  const chosen = parseTypes(files.accepts);
  const known = FILE_TYPES.map((t) => t.ext);
  const other = chosen.filter((e) => !known.includes(e)).join(", ");

  function setTypes(list: string[]) {
    setFiles({ ...files, accepts: Array.from(new Set(list)).join(", ") });
  }
  function toggleType(ext: string) {
    setTypes(chosen.includes(ext) ? chosen.filter((e) => e !== ext) : [...chosen, ext]);
  }

  return (
    <div className="space-y-7">
      <div>
        <h3 className="text-lg font-semibold">Will it work with files?</h3>
        <p className="mt-1 text-[15px] muted">
          For example: someone uploads a spreadsheet and gets a presentation back. Skip this if it
          only answers questions.
        </p>
      </div>

      <section className="space-y-3">
        <p className="label">Can people send it a file?</p>
        <YesNo
          value={wantUpload}
          onChange={(v) => {
            setWantUpload(v);
            if (!v) setFiles({ ...files, upload_volume: "", accepts: "" });
          }}
        />
        {wantUpload ? (
          <div className="space-y-5 pl-1">
            <VolumeField
              label="Where should the files they send be kept?"
              hint="Each file is saved in a folder called “uploads” inside this folder."
              value={files.upload_volume}
              suffix="/uploads"
              onChange={(v) => setFiles({ ...files, upload_volume: v })}
            />
            <div>
              <p className="label">Which kinds of file are allowed?</p>
              <p className="help">Tick the ones you want. Tick none to allow any kind of file.</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {FILE_TYPES.map((t) => {
                  const on = chosen.includes(t.ext);
                  return (
                    <button
                      key={t.ext}
                      type="button"
                      className="step-pill"
                      aria-pressed={on}
                      data-done={on ? "true" : "false"}
                      style={on ? { borderColor: "var(--brand)", background: "var(--brand-soft)", color: "var(--ink)" } : undefined}
                      onClick={() => toggleType(t.ext)}
                    >
                      <span className="step-num">{on ? "✓" : ""}</span>
                      {t.label} (.{t.ext})
                    </button>
                  );
                })}
              </div>
              <label className="mt-3 block">
                <span className="block text-[13px] font-medium muted">Any others? Separate with commas</span>
                <input
                  className="field mt-1 max-w-xs"
                  value={other}
                  placeholder="for example: json, zip"
                  onChange={(e) =>
                    setTypes([...chosen.filter((x) => known.includes(x)), ...parseTypes(e.target.value)])
                  }
                />
              </label>
            </div>
          </div>
        ) : null}
      </section>

      <section className="space-y-3">
        <p className="label">Will it give back a file to download?</p>
        <YesNo
          value={wantOutput}
          onChange={(v) => {
            setWantOutput(v);
            if (!v) setFiles({ ...files, output_volume: "" });
          }}
        />
        {wantOutput ? (
          <div className="pl-1">
            <VolumeField
              label="Where should it save the files it makes?"
              hint="It is told to save results in a folder called “output” inside this folder. Only files saved here can be downloaded."
              value={files.output_volume}
              suffix="/output"
              onChange={(v) => setFiles({ ...files, output_volume: v })}
            />
          </div>
        ) : null}
      </section>

      {(wantUpload || wantOutput) && !alreadyTaught ? (
        <label className="flex cursor-pointer items-start gap-3 rounded-xl p-4" style={{ background: "var(--canvas)", border: "1px solid var(--line)" }}>
          <input
            type="checkbox"
            className="mt-1 h-5 w-5"
            checked={teach}
            onChange={(e) => setTeach(e.target.checked)}
          />
          <span>
            <span className="block text-[15px] font-semibold">Teach it how to use these folders (recommended)</span>
            <span className="help block">
              It only finds out about the files from the chat message. This adds a short note to its
              instructions so it passes the file locations to its tools and tells people where the
              result is. You will see it on the last step.
            </span>
          </span>
        </label>
      ) : null}
    </div>
  );
}

function YesNo({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="grid max-w-sm grid-cols-2 gap-3" role="group">
      <button type="button" className="choice !items-center !justify-center" aria-pressed={!value} onClick={() => onChange(false)}>
        <span className="text-[15px] font-semibold">No</span>
      </button>
      <button type="button" className="choice !items-center !justify-center" aria-pressed={value} onClick={() => onChange(true)}>
        <span className="text-[15px] font-semibold">Yes</span>
      </button>
    </div>
  );
}

// --------------------------------------------------------------- step 5 -----

function Review({
  name,
  description,
  instructions,
  tools,
  catalog,
  files,
  access,
  showPrompt,
  setShowPrompt,
  go,
}: {
  name: string;
  description: string;
  instructions: string;
  tools: BuilderTool[];
  catalog: McpEntry[] | null;
  files: FileSettings;
  access: Access[] | null;
  showPrompt: boolean;
  setShowPrompt: (v: boolean) => void;
  go: (k: StepKey) => void;
}) {
  const types = parseTypes(files.accepts);
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold">Check everything looks right</h3>
        <p className="mt-1 text-[15px] muted">
          Nothing is saved until you press the button at the bottom. Use “Change” to fix anything.
        </p>
      </div>

      <Section title="Name and purpose" onChange={() => go("basics")}>
        <p className="text-[15px] font-semibold">{name || "—"}</p>
        <p className="text-[15px] muted">{description || "No description."}</p>
      </Section>

      <Section title="Instructions" onChange={() => go("behave")}>
        {instructions.trim() ? (
          <>
            <p className={"whitespace-pre-wrap text-[15px] muted " + (showPrompt ? "" : "line-clamp-4")}>
              {instructions}
            </p>
            <button type="button" className="mt-1 text-[13px] underline" onClick={() => setShowPrompt(!showPrompt)}>
              {showPrompt ? "Show less" : "Show all"}
            </button>
          </>
        ) : (
          <p className="text-[15px] muted">None. It will use its general behaviour.</p>
        )}
      </Section>

      <Section title="Abilities" onChange={() => go("abilities")}>
        {tools.length === 0 ? (
          <p className="text-[15px] muted">None added.</p>
        ) : (
          <ul className="space-y-1.5">
            {tools.map((t) => {
              const m = t.type === "app" ? catalog?.find((c) => c.app_name === t.ref) : undefined;
              return (
                <li key={t.type + t.ref} className="text-[15px]">
                  <b>{m ? m.name : ABILITY[t.type]?.title || t.type.replace(/_/g, " ")}</b>
                  {m ? null : <span className="break-all text-[13px] faint"> — {t.ref}</span>}
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <Section title="Files" onChange={() => go("files")}>
        {!files.upload_volume && !files.output_volume ? (
          <p className="text-[15px] muted">It does not work with files.</p>
        ) : (
          <ul className="space-y-1.5 text-[15px]">
            {files.upload_volume ? (
              <li>
                People can send files{types.length ? ` (${types.map((t) => "." + t).join(", ")})` : " of any kind"}.
                <span className="block break-all text-[13px] faint">Kept in {files.upload_volume}</span>
              </li>
            ) : null}
            {files.output_volume ? (
              <li>
                It gives back files to download.
                <span className="block break-all text-[13px] faint">Saved in {files.output_volume}</span>
              </li>
            ) : null}
          </ul>
        )}
      </Section>

      {access ? (
        <Section title="Who can use it" onChange={() => go("access")}>
          {access.length === 0 ? (
            <p className="text-[15px] muted">
              Nobody yet. You can add people later on the Access page.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {access.map((a) => (
                <li key={a.kind + a.principal} className="tag !text-sm">
                  {a.kind === "group" ? "Team: " : "Person: "}
                  {a.principal}
                </li>
              ))}
            </ul>
          )}
        </Section>
      ) : null}
    </div>
  );
}
