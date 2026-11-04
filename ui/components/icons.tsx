import type { ReactNode } from "react";

/** A small set of line icons, drawn for this portal.
 *
 *  Inline SVG on `currentColor`, so they follow the theme and the surrounding
 *  text colour with no image files to ship - the portal has to work inside a
 *  locked-down workspace. All share one 24px grid and stroke, so they read as
 *  a family.
 */
function Svg({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
    >
      {children}
    </svg>
  );
}

type P = { size?: number };

/** A window with its side panel: fold or unfold the chat list. */
export const PanelLeftIcon = ({ size }: P) => (
  <Svg size={size}>
    <rect x="3" y="4" width="18" height="16" rx="3" />
    <path d="M9.5 4v16" />
  </Svg>
);

/** A page with a pencil: start a new chat. */
export const NewChatIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M11 4H7a3 3 0 0 0-3 3v10a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-4" />
    <path d="M17.5 3.5a1.9 1.9 0 0 1 2.7 2.7L12 14.4 8.5 15.5l1.1-3.5z" />
  </Svg>
);

export const SearchIcon = ({ size }: P) => (
  <Svg size={size}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-3.9-3.9" />
  </Svg>
);

export const PaperclipIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="m20.5 11.6-8.3 8.3a5.2 5.2 0 0 1-7.4-7.4l8.6-8.6a3.5 3.5 0 0 1 5 5l-8.6 8.6a1.8 1.8 0 0 1-2.5-2.5l7.9-7.9" />
  </Svg>
);

export const ArrowUpIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 19V5" />
    <path d="m5.5 11.5 6.5-6.5 6.5 6.5" />
  </Svg>
);

export const CopyIcon = ({ size }: P) => (
  <Svg size={size}>
    <rect x="9" y="9" width="11" height="11" rx="2.5" />
    <path d="M5 15V7a3 3 0 0 1 3-3h8" />
  </Svg>
);

export const CheckIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Svg>
);

export const RefreshIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M20 12a8 8 0 1 1-2.6-5.9" />
    <path d="M20 4v5h-5" />
  </Svg>
);

export const TrashIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 7h16" />
    <path d="M9 7V4.5h6V7" />
    <path d="M6.5 7l.8 11.5a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9L17.5 7" />
    <path d="M10 11v6M14 11v6" />
  </Svg>
);

export const ChevronLeftIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="m14.5 6-6 6 6 6" />
  </Svg>
);

/** A four-point sparkle: the assistant's mark, in place of a face. */
export const SparkleIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.3l-1.9-5.5L4.5 10.9 10.1 9z" />
  </Svg>
);

export const SunIcon = ({ size }: P) => (
  <Svg size={size}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" />
  </Svg>
);

export const MoonIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
  </Svg>
);

export const MessageIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-6l-4.5 3.5V17H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" />
  </Svg>
);

export const ThreadsIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 6h12M4 11h16M4 16h9" />
  </Svg>
);

export const UploadIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 16V4M7 9l5-5 5 5" />
    <path d="M4.5 16v2.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V16" />
  </Svg>
);

export const DownloadIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 4v12M7 11l5 5 5-5" />
    <path d="M4.5 16v2.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V16" />
  </Svg>
);

export const ArrowUpRightIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M7 17 17 7M8 7h9v9" />
  </Svg>
);

/** Four tiles: the assistants home. */
export const GridIcon = ({ size }: P) => (
  <Svg size={size}>
    <rect x="4" y="4" width="7" height="7" rx="2" />
    <rect x="13" y="4" width="7" height="7" rx="2" />
    <rect x="4" y="13" width="7" height="7" rx="2" />
    <rect x="13" y="13" width="7" height="7" rx="2" />
  </Svg>
);

/** Rising bars: the dashboard. */
export const ChartIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 20h16" />
    <path d="M7 16v-4M12 16V8M17 16v-7" />
  </Svg>
);

/** A wand with a spark: create an assistant. */
export const WandIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="m4 20 11-11" />
    <path d="m13.5 7.5 3 3" />
    <path d="M18 3v3M16.5 4.5h3M20 9.5v2M19 10.5h2M9.5 3.5v2M8.5 4.5h2" />
  </Svg>
);

/** A padlock: something that cannot be changed here. */
export const LockIcon = ({ size }: P) => (
  <Svg size={size}>
    <rect x="5" y="11" width="14" height="9" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </Svg>
);

/** A pencil: edited by hand. */
export const PencilIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 20h4L19 9l-4-4L4 16z" />
    <path d="m13.5 6.5 4 4" />
  </Svg>
);

/** Sliders: settings. */
export const SlidersIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
    <circle cx="15" cy="7" r="2" />
    <circle cx="9" cy="17" r="2" />
  </Svg>
);

/** A shield: the admin console. */
export const ShieldIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 3.5 19 6v5.5c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6z" />
    <path d="m9 12 2 2 4-4" />
  </Svg>
);

/** Stacked layers: an assistant that combines several tools. */
export const LayersIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="m12 4 8.5 4.5L12 13 3.5 8.5z" />
    <path d="m3.5 12.5 8.5 4.5 8.5-4.5" />
    <path d="m3.5 16.5 8.5 4.5 8.5-4.5" />
  </Svg>
);

/** An open book: answers from documents. */
export const BookIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 6.5C10.5 5 8 4.5 4 4.5v13c4 0 6.5.5 8 2 1.5-1.5 4-2 8-2v-13c-4 0-6.5.5-8 2z" />
    <path d="M12 6.5v13" />
  </Svg>
);

/** Three lines with a handle: open the menu on small screens. */
export const MenuIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Svg>
);

/** One person: your own dashboard. */
export const UserIcon = ({ size }: P) => (
  <Svg size={size}>
    <circle cx="12" cy="8.5" r="3.5" />
    <path d="M5 19.5a7 7 0 0 1 14 0" />
  </Svg>
);

/** Two people: access for people and teams. */
export const UsersIcon = ({ size }: P) => (
  <Svg size={size}>
    <circle cx="9" cy="9" r="3.2" />
    <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
    <path d="M15.5 6.2a3 3 0 0 1 0 5.6M17 14.2a5.5 5.5 0 0 1 3.5 4.8" />
  </Svg>
);

/** A list with ticks: the audit log. */
export const ListIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M9 6.5h11M9 12h11M9 17.5h11" />
    <path d="m3.5 6.5 1 1 2-2M3.5 12l1 1 2-2M3.5 17.5l1 1 2-2" />
  </Svg>
);

/** A pulse line: health. */
export const PulseIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M3 12h4l2.5-6 4 12 2.5-6h5" />
  </Svg>
);

/** A coin: spend. */
export const CoinIcon = ({ size }: P) => (
  <Svg size={size}>
    <circle cx="12" cy="12" r="8" />
    <path d="M14.5 9.2c-.5-.8-1.4-1.2-2.5-1.2-1.5 0-2.6.8-2.6 2s1.1 1.7 2.6 2 2.6.8 2.6 2-1.1 2-2.6 2c-1.1 0-2-.4-2.5-1.2M12 6.5V8M12 16v1.5" />
  </Svg>
);

export const PlusIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const CloseIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);

export const ChevronRightIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="m9.5 6 6 6-6 6" />
  </Svg>
);

export const ChevronDownIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="m6 9.5 6 6 6-6" />
  </Svg>
);

export const ClockIcon = ({ size }: P) => (
  <Svg size={size}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 8v4l2.5 2" />
  </Svg>
);

export const FileIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z" />
    <path d="M14 3.5V8h4.5" />
  </Svg>
);

export const ArrowRightIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Svg>
);

export const ArrowDownRightIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M7 7l10 10M17 8v9H8" />
  </Svg>
);

export const RocketIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M5 15c-1.5 1.3-2 3.6-2 6 2.4 0 4.7-.5 6-2" />
    <path d="M9 18 6 15c.9-3.6 3.4-8.4 9-11.5 1.6-.9 3.6-1.1 5.5-.5.6 1.9.4 3.9-.5 5.5C16.9 14.1 12.6 16.9 9 18Z" />
    <circle cx="15" cy="9" r="1.6" />
  </Svg>
);

export const BuildingIcon = ({ size }: P) => (
  <Svg size={size}>
    <rect x="4" y="3" width="11" height="18" rx="1.5" />
    <path d="M15 9h3.5A1.5 1.5 0 0 1 20 10.5V21M8 7h3M8 11h3M8 15h3M3 21h18" />
  </Svg>
);

export const TagIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M3 12V4.5A1.5 1.5 0 0 1 4.5 3H12l9 9-9 9-9-9Z" />
    <circle cx="8" cy="8" r="1.4" />
  </Svg>
);

export const UndoIcon = ({ size }: P) => (
  <Svg size={size}>
    <path d="M9 14 4 9l5-5" />
    <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
  </Svg>
);
