import type { Setting } from "../api/types";
import { formatInterval } from "./format";
import { t as i18nT } from "./i18n";

export const FAVORITE_MODEL_INTERVAL_KEY = "favorite_model_interval_seconds";
export const REGULAR_MODEL_INTERVAL_KEY = "regular_model_interval_seconds";

const DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS = 300;
const DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS = 600;

export function settingSeconds(settings: Setting[], key: string, fallback: number): number {
  const raw = settings.find((s) => s.key === key)?.value;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed >= 10 ? Math.floor(parsed) : fallback;
}

export function modelStatusIntervals(settings: Setting[]): {
  favorite: number;
  regular: number;
} {
  return {
    favorite: settingSeconds(
      settings,
      FAVORITE_MODEL_INTERVAL_KEY,
      DEFAULT_FAVORITE_MODEL_INTERVAL_SECONDS,
    ),
    regular: settingSeconds(
      settings,
      REGULAR_MODEL_INTERVAL_KEY,
      DEFAULT_REGULAR_MODEL_INTERVAL_SECONDS,
    ),
  };
}

export function modelStatusIntervalLabel(settings: Setting[]): string {
  const intervals = modelStatusIntervals(settings);
  return i18nT("settings.interval.join", {
    a: `${i18nT("settings.interval.favoritePrefix")} ${formatInterval(intervals.favorite)}`,
    sep: i18nT("settings.interval.separator"),
    b: `${i18nT("settings.interval.otherPrefix")} ${formatInterval(intervals.regular)}`,
  });
}

export function modelStatusIntervalValue(settings: Setting[]): string {
  const intervals = modelStatusIntervals(settings);
  return `${formatInterval(intervals.favorite)} / ${formatInterval(intervals.regular)}`;
}
