import { useCallback, useEffect, useState } from "react";
import type { Locale } from "../lib/i18n";
import { setLocale as setLocaleGlobal } from "../lib/i18n";
import { useT } from "./useT";

const KEY = "llm-api-probe-locale";

function getInitial(): Locale {
  if (typeof window === "undefined") return "en";
  const stored = localStorage.getItem(KEY) as Locale | null;
  if (stored === "en" || stored === "zh") return stored;
  // First-visit default: respect the browser's language if it's Chinese.
  return navigator.language?.toLowerCase().startsWith("zh") ? "zh" : "en";
}

/** React hook for the user's preferred UI locale.
 *  - Reads from `localStorage` (key: `llm-api-probe-locale`)
 *  - Falls back to `navigator.language` for first-time visitors
 *  - Default is English
 *  - Mirrors `useTheme` in shape and persistence pattern
 *  - Returns a memoised `t()` that re-renders the component on change */
export function useLocale(): [
  Locale,
  (l: Locale) => void,
  (key: string, vars?: Record<string, string | number>) => string,
] {
  const [locale, setLocaleState] = useState<Locale>(getInitial);
  const t = useT();

  // Keep the module-level t() / <html lang> in sync with the hook state.
  useEffect(() => {
    setLocaleGlobal(locale);
    if (typeof document !== "undefined") {
      document.documentElement.lang = locale;
    }
    try {
      localStorage.setItem(KEY, locale);
    } catch {
      /* private mode etc. — ignore */
    }
  }, [locale]);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
  }, []);

  return [locale, setLocale, t];
}
