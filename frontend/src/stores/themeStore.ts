/**
 * Theme store (PROJECT.md Section 8).
 *
 * Switching is done with a `data-theme` attribute on `<html>`, not a `.dark`
 * class, so the global stylesheet can address both palettes explicitly and
 * neither has to win a specificity race.
 *
 * Resolution order on boot, per Section 8: localStorage, then
 * `prefers-color-scheme`, then dark.
 */

import { create } from "zustand";

export type Theme = "light" | "dark";

const STORAGE_KEY = "policy-rag-theme";

function readStored(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    // Private-mode browsers throw on localStorage access. The theme still has
    // to work, so this falls through to the media query rather than failing.
    return null;
  }
}

function systemTheme(): Theme {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function initialTheme(): Theme {
  return readStored() ?? systemTheme();
}

/**
 * Applied as a module side effect rather than inside a component effect, so the
 * attribute is on `<html>` before React mounts. Otherwise the first paint uses
 * whichever palette the stylesheet happens to default to and the user sees a
 * flash of the wrong theme on every load.
 */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

interface ThemeStore {
  theme: Theme;
  toggle: () => void;
  /** Explicit set, used by the boot sequence and by tests. */
  setTheme: (theme: Theme) => void;
}

export const useThemeStore = create<ThemeStore>((set, get) => ({
  theme: initialTheme(),
  toggle: () => {
    const next: Theme = get().theme === "dark" ? "light" : "dark";
    applyTheme(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Persistence is a convenience; the in-memory theme still applies.
    }
    set({ theme: next });
  },
  setTheme: (theme) => {
    applyTheme(theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // See above.
    }
    set({ theme });
  },
}));
