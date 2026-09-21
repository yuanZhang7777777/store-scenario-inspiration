import { useEffect, useRef, useState } from "react";
import { api, type Job, type ReviewStatus, type ReviewDetails } from "../api";
import BusinessEvidence from "./BusinessEvidence";

interface Props {
  storeId: string;
  status?: ReviewStatus;
  busy: boolean;
  onStarted: (job: Job) => void;
}

export default function ConfirmationPanel({ storeId, status, busy, onStarted }: Props) {
  const [review, setReview] = useState<ReviewDetails | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const submitting = useRef(false);
  useEffect(() => {
    let active = true;
    setReview(null);
    setError("");
    api.review(storeId).then((value) => { if (active) setReview(value); })
      .catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : "无法读取待确认资料。"); });
    return () => { active = false; };
  }, [storeId, status?.version, status?.confirmed, busy]);

  async function confirm() {
    if (submitting.current || busy || !review?.version) return;
    submitting.current = true;
    setSending(true);
    setError("");
    try {
      const next = await api.confirmReview(storeId, review.version);
      onStarted(next);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "未能开始分析，请重试。资料不会重复上传。");
    } finally { submitting.current = false; setSending(false); }
  }
  if (status?.confirmed) return <p className="notice">店铺信息已确认。修改商品范围后需要重新确认。</p>;
  return <section className="card" id="confirm-store-information" aria-label="确认店铺信息">
    <h2>确认店铺信息</h2>
    <p>请检查上方商品，取消不参与分析的商品，并核对下方销售信息。</p>
    <p className="muted">没有销售数据也能继续。确认前不会生成经营分析、场景或匹配 SKU。</p>
    {review?.business_context && <BusinessEvidence context={review.business_context} />}
    {error && <p className="notice error" role="alert">{error}</p>}
    <button className="btn" disabled={busy || sending || !review?.version} onClick={confirm}>
      {sending ? "正在启动…" : "确认信息，开始分析"}
    </button>
  </section>;
}
