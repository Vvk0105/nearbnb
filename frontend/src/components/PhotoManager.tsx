import { useState } from "react";
import { api, ApiError, mediaUrl } from "../lib/api";
import type { Photo } from "../lib/types";
import { Alert, StatusBadge, errorMessage } from "./ui";

/** Upload, delete and reorder photos. `base` is e.g. /host/properties/<id> or /host/units/<id>.
 *  `onChange` receives the updated parent object returned by the API. */
export function PhotoManager<T>({ base, photos, disabled, onChange }: { base: string; photos: Photo[]; disabled?: boolean; onChange: (updated: T) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<T>) => {
    setError("");
    setBusy(true);
    try {
      onChange(await fn());
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setError("");
    setBusy(true);
    try {
      let latest: T | undefined;
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        latest = await api<T>(`${base}/photos`, { method: "POST", form });
      }
      if (latest) onChange(latest);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const move = (index: number, delta: number) => {
    const ids = photos.map((p) => p.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    void run(() => api<T>(`${base}/photos/order`, { method: "PUT", body: { ids } }));
  };

  return (
    <div className="space-y-3">
      {error && <Alert>{error}</Alert>}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {photos.map((p, i) => (
          <div key={p.id} className="overflow-hidden rounded-lg border border-gray-200 bg-white">
            <img src={mediaUrl(p.thumb_url)} alt="" className={`h-32 w-full object-cover ${p.status === "pending_delete" ? "opacity-40" : ""}`} />
            <div className="flex items-center justify-between gap-1 p-2 text-xs">
              <span>{i === 0 ? <strong>Cover</strong> : `#${i + 1}`}{p.status !== "approved" && <> <StatusBadge status={p.status} /></>}</span>
              {!disabled && (
                <span className="flex gap-2">
                  <button aria-label="Move earlier" className="text-gray-500 hover:text-gray-900 disabled:opacity-30" disabled={busy || i === 0} onClick={() => move(i, -1)}>◀</button>
                  <button aria-label="Move later" className="text-gray-500 hover:text-gray-900 disabled:opacity-30" disabled={busy || i === photos.length - 1} onClick={() => move(i, 1)}>▶</button>
                  <button className="text-red-600 hover:underline disabled:opacity-30" disabled={busy || p.status === "pending_delete"} onClick={() => run(() => api<T>(`${base}/photos/${p.id}`, { method: "DELETE" }))}>Delete</button>
                </span>
              )}
            </div>
          </div>
        ))}
        {photos.length === 0 && <p className="col-span-full text-sm text-gray-500">No photos yet.</p>}
      </div>
      {!disabled && (
        <label className="inline-block cursor-pointer rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium hover:bg-gray-50">
          {busy ? "Uploading..." : "Add photos"}
          <input type="file" accept="image/jpeg,image/png,image/webp" multiple className="hidden" disabled={busy} onChange={(e) => { void upload(e.target.files); e.target.value = ""; }} />
        </label>
      )}
    </div>
  );
}
