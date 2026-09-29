/**
 * Theme toggle (PROJECT.md Section 10.3 / 11.1).
 *
 * Section 10.3 requires the switch to cross-fade rather than hard-cut: the
 * surface dims to transparent and back over 150ms while the palette changes
 * underneath, so the repaint is not a single-frame snap.
 *
 * The fade is animated on a fixed overlay rather than on `<html>` itself, for
 * two reasons:
 *
 *  - `data-theme` has to be swapped on `<html>` for the CSS custom properties to
 *    change, and that swap is instant by definition. Animate something *over*
 *    the swap and the change reads as a fade instead of a cut.
 *  - Animating a wrapper would relayout the whole tree, which is what causes the
 *    layout shift this is meant to avoid.
 *
 * The overlay is a single absolutely-positioned layer covering the viewport, so
 * it costs one composited layer and forces no reflow.
 */

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Moon, Sun } from "lucide-react";
import { useThemeStore } from "@/stores/themeStore";

/** Section 10.3: 150ms. */
const FADE_MS = 0.15;

export function ThemeToggle() {
  const theme = useThemeStore((state) => state.theme);
  const toggle = useThemeStore((state) => state.toggle);
  const [isFading, setIsFading] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Cleared on unmount so a toggle-then-navigate cannot set state on an
  // unmounted component or strand the overlay at full opacity.
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const handleToggle = () => {
    if (timer.current) clearTimeout(timer.current);
    setIsFading(true);
    // Applied on the next frame so the overlay is painted before `data-theme`
    // changes; swapping both in the same tick can collapse the fade to nothing.
    requestAnimationFrame(() => {
      toggle();
      timer.current = setTimeout(() => setIsFading(false), FADE_MS * 1000);
    });
  };

  const isDark = theme === "dark";
  const label = isDark ? "Switch to light theme" : "Switch to dark theme";

  return (
    <>
      <button
        type="button"
        onClick={handleToggle}
        aria-label={label}
        title={label}
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-surface hover:text-text-primary"
      >
        {isDark ? (
          <Sun size={16} strokeWidth={1.75} />
        ) : (
          <Moon size={16} strokeWidth={1.75} />
        )}
      </button>

      <AnimatePresence>
        {isFading ? (
          <motion.div
            // Fixed + pointer-events-none, so it covers the app without
            // swallowing the click that started the fade.
            className="pointer-events-none fixed inset-0 z-50 bg-bg"
            // Rises and falls within the same 150ms, so the palette change
            // underneath is masked on the way through without the user seeing a
            // dimmed screen afterwards.
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 0.5, 0] }}
            exit={{ opacity: 0 }}
            transition={{ duration: FADE_MS, ease: "easeOut" }}
            aria-hidden
          />
        ) : null}
      </AnimatePresence>
    </>
  );
}
