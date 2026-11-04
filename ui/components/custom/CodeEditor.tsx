"use client";

import { useEffect, useMemo, useRef } from "react";

/** A small code editor and live view, made for this portal (no editor library to ship or audit, nothing loaded
 *  from another site, so it stays inside the strict CSP). A coloured <pre> sits under a transparent <textarea>
 *  that has the same font, padding and scroll; line numbers on the left; problem lines tinted, with the reason on
 *  hover. `live` shows a blinking cursor at the end and follows the text as it arrives, unless the reader has
 *  scrolled up to look at something. */

const KEYWORDS = new Set(
  ("False None True and as assert async await break class continue def del elif else except finally for from global " +
    "if import in is lambda nonlocal not or pass raise return try while with yield self").split(" ")
);
type Tok = { text: string; cls?: string };

/** Python, coloured line by line; a triple-quoted string may run across lines. */
export function highlight(code: string): Tok[][] {
  const out: Tok[][] = [];
  let triple: string | null = null;
  let afterDef = false;
  for (const line of code.split("\n")) {
    const toks: Tok[] = [];
    let i = 0;
    const push = (text: string, cls?: string) => text && toks.push({ text, cls });
    while (i < line.length) {
      if (triple) {
        const end = line.indexOf(triple, i);
        if (end < 0) {
          push(line.slice(i), "tk-str");
          i = line.length;
        } else {
          push(line.slice(i, end + 3), "tk-str");
          i = end + 3;
          triple = null;
        }
        continue;
      }
      const rest = line.slice(i);
      let m: RegExpMatchArray | null;
      if (rest[0] === "#") {
        push(rest, "tk-com");
        break;
      } else if ((m = rest.match(/^[rbfuRBFU]{0,2}("""|''')/))) {
        triple = m[1];
        push(m[0], "tk-str");
        i += m[0].length;
      } else if ((m = rest.match(/^[rbfuRBFU]{0,2}("(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?)/))) {
        push(m[0], "tk-str");
        i += m[0].length;
      } else if ((m = rest.match(/^@[\w.]+/)) && !toks.some((t) => t.text.trim())) {
        push(m[0], "tk-dec");
        i += m[0].length;
      } else if ((m = rest.match(/^\d[\d_]*(\.\d+)?([eE][+-]?\d+)?/))) {
        push(m[0], "tk-num");
        i += m[0].length;
      } else if ((m = rest.match(/^[A-Za-z_]\w*/))) {
        const w = m[0];
        push(w, KEYWORDS.has(w) ? "tk-kw" : afterDef ? "tk-fn" : undefined);
        afterDef = w === "def" || w === "class";
        i += w.length;
        continue;
      } else {
        push(rest[0]);
        i += 1;
      }
      if (rest[0] !== " ") afterDef = false;
    }
    out.push(toks);
  }
  return out;
}

/** Problems from the checks ("agent/logic.py line 12 (...): why") as {line: why} for one file. */
export function marksFor(path: string, problems: string[]): Record<number, string> {
  const out: Record<number, string> = {};
  const esc = path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp("^" + esc + " line (\\d+)(?: \\((.*?)\\))?: (.*)$");
  for (const p of problems) {
    const m = p.match(re);
    if (m) out[Number(m[1])] = m[3];
  }
  return out;
}

export function CodeEditor({
  value,
  onChange,
  readOnly = false,
  live = false,
  marks = {},
  height = 520,
  label,
}: {
  value: string;
  onChange?: (v: string) => void;
  readOnly?: boolean;
  live?: boolean;
  marks?: Record<number, string>;
  height?: number | string;
  label: string;
}) {
  const lines = useMemo(() => highlight(value), [value]);
  const gutter = useRef<HTMLDivElement>(null);
  const pre = useRef<HTMLPreElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const follow = useRef(true);

  // In the live view, keep the newest line in sight unless the reader scrolled up.
  useEffect(() => {
    const el = scroller.current;
    if (live && el && follow.current) {
      el.scrollTop = el.scrollHeight;
      if (gutter.current) gutter.current.scrollTop = el.scrollTop;
    }
  }, [value, live]);

  const syncFrom = (el: HTMLElement) => {
    if (pre.current && el !== scroller.current) {
      pre.current.scrollTop = el.scrollTop;
      pre.current.scrollLeft = el.scrollLeft;
    }
    if (gutter.current) gutter.current.scrollTop = el.scrollTop;
    if (el === scroller.current) follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const body = (
    <pre className="code-ed-pre" ref={pre} aria-hidden={!readOnly}>
      {lines.map((toks, i) => (
        <span key={i} className={"ln" + (marks[i + 1] ? " bad" : "")} title={marks[i + 1] || undefined}>
          {toks.map((t, j) => (
            <span key={j} className={t.cls}>
              {t.text}
            </span>
          ))}
          {live && i === lines.length - 1 ? <span className="code-caret" aria-hidden /> : null}
        </span>
      ))}
      {/* room for the last line under a horizontal scroll bar */}
      <span className="ln" />
    </pre>
  );

  return (
    <div className="code-ed font-mono text-[12.5px] leading-5" style={{ height }} role="group" aria-label={label}>
      <div className="code-ed-gutter" ref={gutter} aria-hidden>
        {lines.map((_, i) => (
          <span key={i} className={marks[i + 1] ? "bad" : undefined} title={marks[i + 1] || undefined}>
            {i + 1}
          </span>
        ))}
        <span>&nbsp;</span>
      </div>
      <div className="code-ed-body">
        {readOnly ? (
          <div className="code-ed-scroll" ref={scroller} onScroll={(e) => syncFrom(e.currentTarget)} tabIndex={0} aria-label={label}>
            {body}
          </div>
        ) : (
          <>
            {body}
            <textarea
              ref={area}
              className="code-ed-input"
              value={value}
              wrap="off"
              spellCheck={false}
              autoCapitalize="off"
              autoComplete="off"
              aria-label={label}
              onScroll={(e) => syncFrom(e.currentTarget)}
              onChange={(e) => onChange?.(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Tab" && !e.shiftKey) {
                  e.preventDefault();
                  const t = e.currentTarget;
                  t.setRangeText("    ", t.selectionStart, t.selectionEnd, "end");
                  onChange?.(t.value);
                }
              }}
            />
          </>
        )}
      </div>
    </div>
  );
}
