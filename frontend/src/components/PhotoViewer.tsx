import { useEffect } from "react";

export interface Photo {
  src: string;
  label: string;
}

/** The screenshots, one at a time and full size.
 *
 * The operator reads the report against the picture it came from, and a 132px
 * thumbnail is not enough to check a货号 or a sales figure off. Arrow keys move
 * between the shots because comparing two of them is most of the work; Esc, the
 * backdrop and the close button all leave.
 */
export default function PhotoViewer({ photos, index, onIndex, onClose }: {
  photos: Photo[];
  index: number | null;
  onIndex: (index: number) => void;
  onClose: () => void;
}) {
  const open = index !== null && index >= 0 && index < photos.length;
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowRight") onIndex((index! + 1) % photos.length);
      else if (event.key === "ArrowLeft") onIndex((index! - 1 + photos.length) % photos.length);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, index, photos.length, onIndex, onClose]);

  if (!open) return null;
  const photo = photos[index!];
  const step = (delta: number) => onIndex((index! + delta + photos.length) % photos.length);
  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label="查看截图" onClick={onClose}>
      <div className="viewer-bar">
        <span>
          {photo.label}
          {photos.length > 1 && <span className="muted"> {index! + 1} / {photos.length}</span>}
        </span>
        <button className="btn ghost small" onClick={onClose}>关闭</button>
      </div>
      <div className="viewer-stage" onClick={(event) => event.stopPropagation()}>
        {photos.length > 1 && (
          <button className="viewer-step" aria-label="上一张" onClick={() => step(-1)}>‹</button>
        )}
        <img src={photo.src} alt={photo.label} />
        {photos.length > 1 && (
          <button className="viewer-step" aria-label="下一张" onClick={() => step(1)}>›</button>
        )}
      </div>
    </div>
  );
}
