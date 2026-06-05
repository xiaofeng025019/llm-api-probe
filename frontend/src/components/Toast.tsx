import { useEffect, useState } from "react";

export type ToastKind = "ok" | "fail" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string;
}

let _id = 0;
const _emitter = new EventTarget();

export function pushToast(kind: ToastKind, title: string, detail?: string) {
  const t: Toast = { id: ++_id, kind, title, detail };
  _emitter.dispatchEvent(new CustomEvent<Toast>("toast", { detail: t }));
  return t;
}

export function ToastHost() {
  const [items, setItems] = useState<Toast[]>([]);

  useEffect(() => {
    const onPush = (e: Event) => {
      const t = (e as CustomEvent<Toast>).detail;
      setItems((cur) => {
        const withoutDuplicate = cur.filter(
          (x) => !(x.kind === t.kind && x.title === t.title && x.detail === t.detail),
        );
        return [...withoutDuplicate, t].slice(-3);
      });
      setTimeout(() => {
        setItems((cur) => cur.filter((x) => x.id !== t.id));
      }, 4500);
    };
    _emitter.addEventListener("toast", onPush);
    return () => _emitter.removeEventListener("toast", onPush);
  }, []);

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
