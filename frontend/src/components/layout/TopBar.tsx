/**
 * Top bar (PROJECT.md Section 11.1).
 *
 * Wordmark, placeholder search, theme toggle. Section 9.4 says density over
 * padding, so this is a 48px bar rather than a roomy navbar.
 */

import { ThemeToggle } from "./ThemeToggle";

export function TopBar() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-border bg-surface px-4">
      <span className="font-display text-[15px] font-semibold tracking-tight text-text-primary">
        Policy Intelligence
      </span>

      <div className="flex-1" />

      {/*
        Placeholder per Section 11.1. It is inert rather than a half-built
        search: there is no search endpoint in Section 6, and a control that
        accepts input and does nothing would be worse than an obviously
        inactive one.
      */}
      <div className="hidden items-center gap-2 rounded-md border border-border bg-bg px-2.5 py-1 text-[13px] text-text-muted md:flex">
        <span>Search policies…</span>
        <kbd className="rounded border border-border px-1 text-[11px] text-text-muted">
          coming soon
        </kbd>
      </div>

      <ThemeToggle />
    </header>
  );
}
