import { useEffect, useState, useCallback } from "react";

export type ToastKind = "ok" | "fail" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string;
}

let _id = 0;
const listeners: Array<(t: Toast) => void> = [];

export function pushToast(kind: ToastKind, title: string, detail?: string) {
  const t: Toast = { id: ++_id, kind, title, detail };
  for (const l of listeners) l(t);
  return t;
}

export function ToastHost() {
  const [items, setItems] = useState<Toast[]>([]);

  const onPush = useCallback((t: Toast) => {
    setItems((cur) => [...cur, t]);
    setTimeout(() => {
      setItems((cur) => cur.filter((x) => x.id !== t.id));
    }, 4500);
  }, []);

  useEffect(() => {
    listeners.push(onPush);
    return () => {
      const i = listeners.indexOf(onPush);
      if (i >= 0) listeners.splice(i, 1);
    };
  }, [onPush]);

  return (
    <div className="toast-host" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`} role="alert">
          <span className="toast-dot" />
          <div>
            <div className="toast-title">{t.title}</div>
            {t.detail && <div className="toast-detail">{t.detail}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}
