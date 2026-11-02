"use client";

import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { Agent, api, SavedChat, Session } from "@/lib/api";
import { kindMeta } from "@/components/AgentCard";
import { Chat } from "@/components/Chat";
import { Admin } from "@/components/Admin";
import { AuditLogPage, MonitoringPage } from "@/components/Ops";
import { Builder } from "@/components/Builder";
import { Approvals } from "@/components/custom/Approvals";
import { Dashboard, Insights } from "@/components/Dashboard";
import { Home, whenAgo } from "@/components/Home";
import { Page as SwitcherPage, QuickSwitcher } from "@/components/QuickSwitcher";
import { ErrorBox, Spinner } from "@/components/bits";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  ChartIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CloseIcon,
  GridIcon,
  ListIcon,
  MenuIcon,
  PulseIcon,
  SearchIcon,
  UserIcon,
  UsersIcon,
  WandIcon,
} from "@/components/icons";
import { initials, nameOf } from "@/lib/people";
import { applyBrand } from "@/lib/brand";
import { PORTAL_LOGO, PORTAL_NAME } from "@/lib/portalLogo";

/** Every section: one place each, in the order people need them. Workspace is
 *  for everyone; Manage is for admins and replaces the old tabbed admin console
 *  so nothing is nested two levels deep or shown twice. */
const SECTIONS: { value: string; label: string; icon: (s?: number) => ReactNode; admin?: boolean }[] = [
  { value: "agents", label: "Assistants", icon: (s) => <GridIcon size={s} /> },
  { value: "dashboard", label: "My dashboard", icon: (s) => <UserIcon size={s} /> },
  // Shown to admins, and to anyone with an agent waiting for their approval (see `shown`).
  { value: "approvals", label: "Approvals", icon: (s) => <CheckIcon size={s} /> },
  { value: "insights", label: "Insights", icon: (s) => <ChartIcon size={s} />, admin: true },
  { value: "access", label: "Access", icon: (s) => <UsersIcon size={s} />, admin: true },
  { value: "build", label: "Build", icon: (s) => <WandIcon size={s} />, admin: true },
  { value: "monitoring", label: "Monitoring", icon: (s) => <PulseIcon size={s} />, admin: true },
  { value: "audit", label: "Audit log", icon: (s) => <ListIcon size={s} />, admin: true },
];
const TITLES: Record<string, string> = Object.fromEntries(SECTIONS.map((s) => [s.value, s.label]));
// Old links keep working: the tabbed admin console's address now opens Access.
const LEGACY: Record<string, string> = { admin: "access", activity: "audit", health: "monitoring" };

export default function Page() {
  const [session, setSession] = useState<Session | null>(null);
  const [fatal, setFatal] = useState("");

  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [waiting, setWaiting] = useState(0);
  const [agentsErr, setAgentsErr] = useState("");
  const [recents, setRecents] = useState<SavedChat[] | null>(null);

  const [open, setOpen] = useState<Agent | null>(null);
  const [resumeId, setResumeId] = useState("");
  const [tab, setTab] = useState("agents");
  const [collapsed, setCollapsed] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [switcher, setSwitcher] = useState(false);
  const [mac, setMac] = useState(false);
  const headerRef = useRef<HTMLElement>(null);

  // Tabs are reflected in the URL so a section can be linked to and survives
  // a refresh.
  useEffect(() => {
    const raw = window.location.hash.replace("#", "");
    const want = LEGACY[raw] || raw;
    if (SECTIONS.some((s) => s.value === want)) setTab(want);
    try {
      setCollapsed(localStorage.getItem("agent-portal-nav-collapsed") === "1");
    } catch {
      // Blocked storage just means the menu opens expanded each visit.
    }
    setMac(/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent));
  }, []);

  function pickTab(next: string) {
    setTab(next);
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", next === "agents" ? "#" : "#" + next);
    }
  }

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem("agent-portal-nav-collapsed", next ? "1" : "0");
    } catch {
      // Only a preference; it resets on the next visit.
    }
  }

  useEffect(() => {
    api
      .session()
      .then(setSession)
      .catch((e) => setFatal(e.message));
  }, []);

  // The client's branding: colours (cached for the next visit's first
  // paint), the tab title and the browser icon.
  const brandName = session?.brand?.name || PORTAL_NAME;
  const brandLogo = session?.brand?.logo || PORTAL_LOGO;
  useEffect(() => {
    if (!session) return;
    applyBrand(session.brand?.color || "");
    document.title = brandName;
    let link = document.querySelector("link[rel='icon']") as HTMLLinkElement | null;
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.href = brandLogo;
  }, [session, brandName, brandLogo]);

  const loadAgents = useCallback(async () => {
    try {
      const d = await api.agents();
      setAgents(d.agents);
      return d.agents;
    } catch (e: any) {
      setAgentsErr(e.message);
      return [] as Agent[];
    }
  }, []);

  const loadWaiting = useCallback(() => {
    api
      .approvals()
      .then((r) => setWaiting(r.items.length))
      .catch(() => setWaiting(0));
  }, []);

  useEffect(() => {
    if (!session) return;
    loadAgents();
    loadWaiting();
  }, [session, loadAgents, loadWaiting]);

  // A custom agent's version still being prepared is open only to its builders and approvers, from
  // its own page: it is not listed with the assistants people use.
  const listed = useMemo(() => (agents ? agents.filter((a) => !a.preview) : null), [agents]);
  const shown = (v: string) => v !== "approvals" || !!session?.is_admin || waiting > 0;

  // Open an assistant by its name, reading the list again first (a draft just saved is not in it yet).
  async function openKey(key: string) {
    const list = await loadAgents();
    const a = list.find((x) => x.name === key);
    if (a) openAgent(a);
    else setAgentsErr("That agent is not available to you yet. If it was just saved, wait for its documents to be read.");
  }

  // Recent conversations, across every assistant. Refreshed whenever a chat is
  // closed so what was just said shows up straight away.
  const loadRecents = useCallback(() => {
    if (!session?.chat_history) return;
    api
      .chats("")
      .then((r) => setRecents(r.chats))
      .catch(() => setRecents([]));
  }, [session]);

  useEffect(() => {
    if (!open) loadRecents();
  }, [open, loadRecents]);

  useEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    const set = () => document.documentElement.style.setProperty("--header-h", el.offsetHeight + "px");
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, [session]);

  // Ctrl/Cmd + K opens the quick switcher from anywhere.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSwitcher((v) => !v);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const pages = useMemo<SwitcherPage[]>(
    () =>
      SECTIONS.filter((s) => (!s.admin || session?.is_admin) && shown(s.value)).map((s) => ({ value: s.value, label: s.label, icon: s.icon(18) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [session, waiting]
  );

  function openAgent(a: Agent) {
    setResumeId("");
    setOpen(a);
    setDrawer(false);
  }

  function resumeChat(a: Agent, id: string) {
    setResumeId(id);
    setOpen(a);
    setDrawer(false);
  }

  function goPage(v: string) {
    setOpen(null);
    setDrawer(false);
    pickTab(v);
  }

  if (fatal) {
    return (
      <Splash>
        <div className="w-full max-w-md">
          <ErrorBox>{fatal}</ErrorBox>
        </div>
      </Splash>
    );
  }

  if (!session) {
    return (
      <Splash>
        <Spinner label="Signing you in…" />
      </Splash>
    );
  }

  // During a conversation the menu folds to its icon rail on wide screens, so
  // the chat and its own history list get the room.
  const rail = collapsed || !!open;
  const byName = new Map((agents || []).map((a) => [a.name, a]));
  const sideRecents = (recents || []).filter((c) => byName.get(c.endpoint)?.ready).slice(0, 6);
  const shortName = session.display_name.includes("@") ? nameOf(session.display_name) : session.display_name;
  // The running version, for admins: it is what the deployer shows for this
  // client, so support can match the two at a glance.
  const role =
    (session.is_admin ? "Administrator" : "Member") +
    (session.auth_mode === "local-dev" ? " · local" : "") +
    (session.is_admin && session.version ? ` · ${session.version}` : "");

  return (
    <Tabs.Root
      value={tab}
      orientation="vertical"
      onValueChange={(v) => {
        // Navigating always leaves an open chat, so the menu works from anywhere.
        setOpen(null);
        setDrawer(false);
        pickTab(v);
      }}
    >
      <div className="flex min-h-dvh">
        {/* Phone: dim the page behind the open menu. */}
        {drawer ? (
          <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setDrawer(false)} aria-hidden />
        ) : null}

        <aside
          className={`side fixed inset-y-0 left-0 z-50 flex w-[280px] flex-col transition-[width,transform] duration-200 lg:sticky lg:top-0 lg:z-30 lg:h-dvh lg:translate-x-0 ${
            drawer ? "translate-x-0" : "-translate-x-full"
          } ${rail ? "lg:w-[76px]" : "lg:w-[264px]"}`}
          aria-label="Main menu"
        >
          {/* Brand */}
          <div className={`flex h-16 shrink-0 items-center gap-3 px-5 ${rail ? "lg:justify-center lg:px-0" : ""}`}>
            <button type="button" className="flex min-w-0 items-center gap-3" onClick={() => goPage("agents")} title={`${brandName} home`}>
              <BrandMark logo={brandLogo} />
              <span className={`min-w-0 text-left leading-tight ${rail ? "lg:hidden" : ""}`}>
                <span className="block truncate text-[15px] font-semibold tracking-[-0.01em]">{brandName}</span>
                <span className="block truncate text-[12px]" style={{ color: "var(--side-faint)" }}>
                  AI assistants for your team
                </span>
              </span>
            </button>
            <button
              type="button"
              className="side-icon-btn ml-auto lg:hidden"
              onClick={() => setDrawer(false)}
              aria-label="Close menu"
            >
              <CloseIcon />
            </button>
          </div>

          <div className={`px-3 ${rail ? "lg:px-[18px]" : ""}`}>
            <button
              type="button"
              className={`side-search ${rail ? "lg:justify-center lg:px-0" : ""}`}
              onClick={() => setSwitcher(true)}
              title="Search assistants and pages"
            >
              <SearchIcon size={18} />
              <span className={`flex-1 ${rail ? "lg:hidden" : ""}`}>Search</span>
              <span className={`kbd hidden ${rail ? "" : "lg:inline-flex"}`}>{mac ? "⌘K" : "Ctrl K"}</span>
            </button>
          </div>

          <nav className="side-scroll mt-2 min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 pb-4">
            <Tabs.List aria-label="Sections" className="flex flex-col gap-0.5">
              <p className={`side-label ${rail ? "lg:sr-only" : ""}`}>Workspace</p>
              {SECTIONS.filter((s) => !s.admin && shown(s.value)).map((s) => (
                <NavItem
                  key={s.value}
                  value={s.value}
                  icon={s.icon()}
                  label={s.label}
                  rail={rail}
                  count={s.value === "agents" ? listed?.length : s.value === "approvals" && waiting ? waiting : undefined}
                />
              ))}
              {session.is_admin ? (
                <>
                  <p className={`side-label ${rail ? "lg:sr-only" : ""}`}>Manage</p>
                  {SECTIONS.filter((s) => s.admin).map((s) => (
                    <NavItem key={s.value} value={s.value} icon={s.icon()} label={s.label} rail={rail} />
                  ))}
                </>
              ) : null}
            </Tabs.List>

            {sideRecents.length ? (
              <div className={rail ? "lg:hidden" : ""}>
                <p className="side-label">Recent</p>
                <ul className="flex flex-col gap-0.5">
                  {sideRecents.map((c) => {
                    const a = byName.get(c.endpoint)!;
                    return (
                      <li key={c.id}>
                        <button
                          type="button"
                          className="side-recent"
                          onClick={() => resumeChat(a, c.id)}
                          title={`${c.title} · ${a.display_name}`}
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block truncate" style={{ color: "var(--side-ink)" }}>
                              {c.title || "Untitled conversation"}
                            </span>
                            <span className="block truncate text-[12px]" style={{ color: "var(--side-faint)" }}>
                              {a.display_name} · {whenAgo(c.updated)}
                            </span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}
          </nav>

          {/* Who is signed in, and the fold control */}
          <div className="shrink-0 border-t p-3" style={{ borderColor: "var(--side-line)" }}>
            <div className={`flex items-center gap-3 rounded-lg p-1.5 ${rail ? "lg:flex-col lg:gap-2 lg:p-0" : ""}`} title={session.user_name}>
              <span
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[13px] font-semibold"
                style={{ background: "linear-gradient(135deg, var(--mark-from), var(--mark-to))", color: "#fff" }}
                aria-hidden
              >
                {initials(session.display_name)}
              </span>
              <span className={`min-w-0 flex-1 leading-tight ${rail ? "lg:hidden" : ""}`}>
                <span className="block truncate text-sm font-semibold">{shortName}</span>
                <span className="block truncate text-[12px]" style={{ color: "var(--side-faint)" }}>
                  {role}
                </span>
              </span>
              {!open ? (
                <button
                  type="button"
                  className="side-icon-btn hidden lg:inline-flex"
                  onClick={toggleCollapsed}
                  title={collapsed ? "Expand menu" : "Collapse menu"}
                  aria-label={collapsed ? "Expand menu" : "Collapse menu"}
                  aria-expanded={!collapsed}
                >
                  {collapsed ? <ChevronRightIcon /> : <ChevronLeftIcon />}
                </button>
              ) : null}
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header ref={headerRef} className="topbar sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 px-4 lg:px-8">
            <button
              type="button"
              className="icon-btn lg:hidden"
              onClick={() => setDrawer(true)}
              aria-label="Open menu"
              aria-expanded={drawer}
            >
              <MenuIcon />
            </button>
            <nav aria-label="You are here" className="flex min-w-0 items-center gap-2 text-sm">
              {open ? (
                <>
                  <button type="button" className="shrink-0 muted hover:underline" onClick={() => setOpen(null)}>
                    Assistants
                  </button>
                  <span className="faint" aria-hidden>
                    <ChevronRightIcon size={14} />
                  </span>
                  <span className="flex min-w-0 items-center gap-2 font-semibold">
                    <span className={`kind-tile !h-6 !w-6 !rounded-md [&_svg]:h-3.5 [&_svg]:w-3.5 ${kindMeta(open.kind).cls}`} aria-hidden>
                      {kindMeta(open.kind).icon}
                    </span>
                    <span className="truncate">{open.display_name}</span>
                  </span>
                </>
              ) : (
                <>
                  <span className="hidden max-w-[220px] shrink-0 truncate muted sm:inline">{brandName}</span>
                  <span className="hidden faint sm:inline" aria-hidden>
                    <ChevronRightIcon size={14} />
                  </span>
                  <span className="truncate font-semibold">{TITLES[tab]}</span>
                </>
              )}
            </nav>
            <div className="ml-auto flex items-center gap-1.5">
              <button
                type="button"
                className="btn btn-quiet hidden !min-h-[36px] !py-1 md:inline-flex"
                onClick={() => setSwitcher(true)}
              >
                <SearchIcon size={16} />
                <span className="text-[13px]">Search</span>
                <span className="kbd">{mac ? "⌘K" : "Ctrl K"}</span>
              </button>
              <button type="button" className="icon-btn md:hidden" onClick={() => setSwitcher(true)} aria-label="Search">
                <SearchIcon />
              </button>
              <ThemeToggle />
            </div>
          </header>

          <main className={open ? "w-full" : "mx-auto w-full max-w-[1440px] px-4 pb-20 pt-6 sm:px-6 lg:px-10 lg:pt-8"}>
            {open ? (
              <Chat
                agent={open}
                agents={listed || []}
                historyEnabled={session.chat_history}
                resumeId={resumeId}
                onBack={() => setOpen(null)}
                onSwitch={openAgent}
              />
            ) : (
              <>
                <Tabs.Content value="agents" className="outline-none">
                  <Home
                    session={session}
                    agents={listed}
                    agentsErr={agentsErr}
                    recents={session.chat_history ? recents : []}
                    onOpen={openAgent}
                    onResume={resumeChat}
                  />
                </Tabs.Content>

                <Tabs.Content value="dashboard" className="outline-none">
                  <Dashboard onGoto={goPage} agents={listed || []} onOpen={openAgent} onResume={resumeChat} />
                </Tabs.Content>

                {shown("approvals") ? (
                  <Tabs.Content value="approvals" className="outline-none">
                    <PageHead
                      eyebrow="Workspace"
                      title="Approvals"
                      text="Agents someone asked you to approve. Read what each does and its test answers, try it, then approve it or send it back."
                    />
                    <Approvals onTry={openKey} onChanged={() => { loadWaiting(); loadAgents(); }} />
                  </Tabs.Content>
                ) : null}

                {session.is_admin ? (
                  <>
                    <Tabs.Content value="insights" className="outline-none">
                      <Insights agents={listed || []} onGoto={goPage} />
                    </Tabs.Content>
                    <Tabs.Content value="access" className="outline-none">
                      <PageHead
                        eyebrow="Manage"
                        title="Access"
                        text="Who can use each assistant. Pick one to add or remove people and teams, or to change its name, description and file settings."
                      />
                      <Admin />
                    </Tabs.Content>
                    <Tabs.Content value="build" className="outline-none">
                      <Builder onGoto={pickTab} onTry={openKey} />
                    </Tabs.Content>
                    <Tabs.Content value="monitoring" className="outline-none">
                      <PageHead
                        eyebrow="Manage"
                        title="Monitoring"
                        text="Reliability and errors for every assistant, with the cause of each error and how to resolve it."
                      />
                      <MonitoringPage />
                    </Tabs.Content>
                    <Tabs.Content value="audit" className="outline-none">
                      <PageHead
                        eyebrow="Manage"
                        title="Audit log"
                        text="A record of who did what in the portal, and of access changes recorded by Databricks. Message content is never logged."
                      />
                      <AuditLogPage />
                    </Tabs.Content>
                  </>
                ) : null}
              </>
            )}
          </main>
        </div>
      </div>

      <QuickSwitcher
        open={switcher}
        onClose={() => setSwitcher(false)}
        agents={listed || []}
        pages={pages}
        onAgent={openAgent}
        onPage={goPage}
      />
    </Tabs.Root>
  );
}

function NavItem({
  value,
  icon,
  label,
  rail,
  count,
}: {
  value: string;
  icon: ReactNode;
  label: string;
  rail: boolean;
  count?: number;
}) {
  return (
    <Tabs.Trigger value={value} className={`side-link ${rail ? "lg:justify-center lg:px-0" : ""}`} title={label}>
      <span className="shrink-0">{icon}</span>
      <span className={`flex-1 truncate ${rail ? "lg:sr-only" : ""}`}>{label}</span>
      {count ? (
        <span
          className={`rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums ${rail ? "lg:hidden" : ""}`}
          style={{ background: "var(--side-chip)", color: "var(--side-dim)" }}
        >
          {count}
        </span>
      ) : null}
    </Tabs.Trigger>
  );
}

function PageHead({ eyebrow, title, text }: { eyebrow: string; title: string; text: string }) {
  return (
    <div className="mb-7">
      <p className="text-[13px] font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--brand-deep)" }}>
        {eyebrow}
      </p>
      <h1 className="mt-1 text-[28px] font-semibold leading-tight tracking-[-0.02em]">{title}</h1>
      <p className="mt-1.5 max-w-2xl text-[15px] muted">{text}</p>
    </div>
  );
}

/** The brand tile: the client's logo when they have one, else the MediaMint logo. */
function BrandMark({ logo, big }: { logo?: string; big?: boolean }) {
  const size = big ? "!h-12 !w-12 !rounded-2xl" : "";
  const own = logo && logo !== PORTAL_LOGO; // a client's logo is framed on white; ours fills the tile
  return (
    <span className={`side-mark has-logo ${size}`} style={own ? undefined : { padding: 0, background: "transparent" }} aria-hidden>
      <img src={logo || PORTAL_LOGO} alt="" />
    </span>
  );
}

function Splash({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-6 px-4">
      <BrandMark big />
      <p className="text-lg font-semibold tracking-[-0.01em]">{PORTAL_NAME}</p>
      {children}
    </main>
  );
}
