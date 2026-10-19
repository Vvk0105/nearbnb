import { useEffect, useState } from "react";
import { mediaUrl } from "../lib/api";
import type { Photo } from "../lib/types";
import { Icon } from "./icons";

/** A photo mosaic (one big picture and up to four small ones) that opens a full-screen viewer. */
export function Gallery({ photos, name }: { photos: Photo[]; name: string }) {
  const [viewer, setViewer] = useState<number | null>(null);
  const count = photos.length;

  useEffect(() => {
    if (viewer === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setViewer(null);
      if (e.key === "ArrowRight") setViewer((i) => (i === null ? i : (i + 1) % count));
      if (e.key === "ArrowLeft") setViewer((i) => (i === null ? i : (i - 1 + count) % count));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [viewer, count]);

  if (count === 0) {
    return (
      <div className="flex aspect-[16/7] flex-col items-center justify-center gap-2 rounded-2xl bg-slate-100 text-slate-400">
        <Icon name="home" className="h-10 w-10" />
        No photos yet
      </div>
    );
  }

  const alt = (i: number) => `${name}, photo ${i + 1} of ${count}`;
  const side = photos.slice(1, 5);
  return (
    <>
      <div className={`relative grid gap-2 overflow-hidden rounded-2xl ${side.length > 0 ? "md:grid-cols-4 md:grid-rows-2" : ""}`}>
        <button type="button" onClick={() => setViewer(0)} aria-label="Open photo 1" className={`block overflow-hidden ${side.length > 0 ? "md:col-span-2 md:row-span-2" : ""}`}>
          <img src={mediaUrl(photos[0].url)} alt={alt(0)} className="aspect-[4/3] h-full w-full object-cover transition hover:scale-[1.02] md:aspect-auto md:min-h-[22rem]" />
        </button>
        {side.map((p, i) => (
          <button key={p.id} type="button" onClick={() => setViewer(i + 1)} aria-label={`Open photo ${i + 2}`} className="hidden overflow-hidden md:block">
            <img src={mediaUrl(p.thumb_url)} alt={alt(i + 1)} className="h-full w-full object-cover transition hover:scale-105" />
          </button>
        ))}
        {count > 1 && (
          <button type="button" onClick={() => setViewer(0)} className="absolute bottom-3 right-3 rounded-lg bg-white/95 px-3 py-1.5 text-sm font-medium shadow hover:bg-white">
            Show all {count} photos
          </button>
        )}
      </div>

      {viewer !== null && (
        <div role="dialog" aria-modal="true" aria-label={`${name} photos`} className="fixed inset-0 z-50 flex flex-col bg-black/95">
          <div className="flex items-center justify-between p-4 text-white">
            <span className="text-sm">{viewer + 1} / {count}</span>
            <button type="button" aria-label="Close photos" onClick={() => setViewer(null)} className="rounded-full p-2 hover:bg-white/10"><Icon name="close" /></button>
          </div>
          <div className="relative flex flex-1 items-center justify-center px-12 pb-6">
            <img src={mediaUrl(photos[viewer].url)} alt={alt(viewer)} className="max-h-full max-w-full rounded-lg object-contain" />
            {count > 1 && (
              <>
                <button type="button" aria-label="Previous photo" onClick={() => setViewer((viewer - 1 + count) % count)} className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-3 text-white hover:bg-white/20"><Icon name="arrow" className="h-5 w-5 rotate-180" /></button>
                <button type="button" aria-label="Next photo" onClick={() => setViewer((viewer + 1) % count)} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-3 text-white hover:bg-white/20"><Icon name="arrow" className="h-5 w-5" /></button>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
