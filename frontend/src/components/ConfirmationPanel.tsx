import { useEffect, useRef, useState } from "react";
import { api, type Job, type ReviewStatus, type ReviewDetails } from "../api";
import BusinessEvidence from "./BusinessEvidence";

interface Props {
  storeId: string;
  status?: ReviewStatus;
  busy: boolean;
  productCount?: number;
  beforeConfirm?: () => Promise<void>;
  onReload?: () => Promise<void>;
  onBusyChange?: (busy: boolean) => void;
  onStarted: (job: Job) => void;
}

export default function ConfirmationPanel({ storeId, status, busy, productCount, beforeConfirm, onReload, onBusyChange, onStarted }: Props) {
  const [review, setReview] = useState<ReviewDetails | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [reload, setReload] = useState(0);
  const submitting = useRef(false);
  useEffect(() => {
    let active = true;
    setReview(null);
    setError("");
    if (!status?.confirmed) {
      api.review(storeId).then((value) => { if (active) setReview(value); })
        .catch(() => { if (active) setError("资料暂时无法加载，请重试。"); });
    }
    return () => { active = false; };
  }, [storeId, status?.version, status?.confirmed, reload]);

  async function confirm() {
    if (submitting.current || busy || !review?.version || productCount === 0) return;
    // Preserve the version the operator actually reviewed. Never silently
    // approve newer source data merely because a request is being retried.
    const reviewedVersion = review.version;
    submitting.current = true;
    setSending(true);
    onBusyChange?.(true);
    setError("");
    try {
      await beforeConfirm?.();
      const next = await api.confirmReview(storeId, reviewedVersion);
      onStarted(next);
    } catch (failure: unknown) {
      setError(failure instanceof Error ? failure.message : "未能开始分析，请重试。");
    } finally {
      submitting.current = false;
      setSending(false);
      onBusyChange?.(false);
    }
  }

  if (status?.confirmed) return null;
  return <section className="card operator-confirm" id="confirm-store-information" aria-label="核对商品">
    <span className="operator-kicker">下一步</span>
    <h2>这些是你店里的商品吗？</h2>
    <p className="muted">上方取消勾选不相关的商品，漏掉的可以补充。核对后生成经营建议。</p>
    {review?.business_context && <BusinessEvidence context={review.business_context} />}
    {productCount === 0 && <p className="notice warn">请至少保留一件商品。</p>}
    {!review && !error && <p className="muted" role="status">正在加载待确认资料…</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
    <div className="form-actions">
      <button className="btn" disabled={busy || sending || !review?.version || productCount === 0} onClick={confirm}>
        {sending ? "正在启动…" : "确认商品，生成经营建议"}
      </button>
      {error && <button className="btn ghost small" disabled={busy || sending} onClick={async () => {
        setSending(true); onBusyChange?.(true);
        try { await onReload?.(); setReload((value) => value + 1); }
        catch { setError("资料未能刷新，请重试。"); }
        finally { setSending(false); onBusyChange?.(false); }
      }}>重新加载资料</button>}
    </div>
  </section>;
}
