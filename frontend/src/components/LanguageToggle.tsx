import { useEffect, useRef, useState } from "react";
import { useLocale } from "../hooks/useLocale";
import type { Locale } from "../lib/i18n";
import { IconGlobe } from "./Icons";

const LOCALES: ReadonlyArray<{ code: Locale; label: string }> = [
  { code: "en", label: "English" },
  { code: "zh", label: "中文" },
];

export function LanguageToggle() {
  const [locale, setLocale, t] = useLocale();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="language-toggle-wrap" ref={wrapRef}>
      <button
        type="button"
        className="theme-toggle"
        onClick={() => setOpen((o) => !o)}
        title={t("language.name")}
        aria-label={t("language.name")}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span aria-hidden="true">
          <IconGlobe />
        </span>
      </button>
      {open && (
        <ul className="language-menu" role="menu">
          {LOCALES.map((l) => (
            <li key={l.code} role="none">
              <button
                type="button"
                role="menuitemradio"
                aria-checked={locale === l.code}
                onClick={() => {
                  setLocale(l.code);
                  setOpen(false);
                }}
                className={locale === l.code ? "active" : ""}
              >
                <span>{l.label}</span>
                {locale === l.code && (
                  <span className="language-menu-check" aria-hidden="true">
                    ✓
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
