import { useCallback, useEffect, useState } from "react";
import { getRevision, subscribeLocale, t as tCore } from "../lib/i18n";

/** React hook: returns a memoised `t(key, vars?)` that re-renders the
 *  component when the module-level locale changes. Use in components
 *  instead of the bare `t()` so they actually re-render on switch. */
export function useT(): (
  key: string,
  vars?: Record<string, string | number>,
) => string {
  const [rev, setRev] = useState(getRevision);
  useEffect(() => {
    const handler = () => setRev(getRevision());
    const unsubscribe = subscribeLocale(handler);
    return unsubscribe;
  }, []);
  // Re-read t on every call; the locale is captured at module scope.
  return useCallback(
    (key, vars) => tCore(key, vars),
    // rev is just a bump counter — invalidate the callback on locale change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rev],
  );
}
