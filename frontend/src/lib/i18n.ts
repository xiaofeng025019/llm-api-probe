/** Lightweight i18n: typed messages, no external dependencies. */

import en from "../locales/en";
import zh from "../locales/zh";

export type Locale = "en" | "zh";

/** Structural type: any nested object whose leaves are strings. Both locale
 *  files must conform to this shape, but the string *values* can differ. */
export type Messages = {
  readonly [K: string]: string | { readonly [K2: string]: string | Messages };
};

/** Recursively derive dotted-key union from a messages object. The leaves
 *  are the union of all dotted paths through the structure. */
type Primitive = string | number | boolean;
type Join<K, P> = K extends string | number
  ? P extends string | number
    ? `${K}.${P}`
    : never
  : never;
export type Key<T = Messages> = {
  [K in keyof T]: T[K] extends Primitive
    ? `${K & string}`
    : T[K] extends object
      ? `${K & string}` | Join<K & string, Key<T[K]>>
      : never;
}[keyof T];

const MESSAGES: Record<Locale, Messages> = { en, zh };

let _locale: Locale = "en";
let _revision = 0;
const _bus = new EventTarget();

export function getLocale(): Locale {
  return _locale;
}

export function setLocale(l: Locale): void {
  if (_locale === l) return;
  _locale = l;
  _revision += 1;
  _bus.dispatchEvent(new Event("change"));
  if (typeof document !== "undefined") {
    document.documentElement.lang = l;
  }
}

export function getRevision(): number {
  return _revision;
}

export function subscribeLocale(listener: () => void): () => void {
  _bus.addEventListener("change", listener);
  return () => _bus.removeEventListener("change", listener);
}

/** Translate a key. Falls back to the key itself when missing. Supports
 *  simple `${name}` substitution via the `vars` object. */
export function t(
  key: Key | string,
  vars?: Record<string, string | number>,
): string {
  const parts = String(key).split(".");
  let value: unknown = MESSAGES[_locale];
  for (const p of parts) {
    if (value && typeof value === "object") {
      value = (value as Record<string, unknown>)[p];
    } else {
      value = undefined;
      break;
    }
  }
  if (typeof value !== "string") return String(key);
  if (!vars) return value;
  return value.replace(/\$\{(\w+)\}/g, (_, k) => {
    const v = vars[k];
    return v == null ? "" : String(v);
  });
}
