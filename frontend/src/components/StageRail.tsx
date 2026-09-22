import type { StageStatus } from "../api";
import { groupStatus } from "../operatorUx";

export interface RailStage {
  name: string;
  label: string;
  status: StageStatus;
  note: string;
  target: string;
}
interface Props {
  stages: RailStage[];
  summary: string;
  running: boolean;
  onCancel: () => void;
}
const GROUPS = [
  { label: "识别店铺商品", names: ["recognize", "clues"], target: "clues" },
  { label: "生成经营建议", names: ["synthesis", "scenes", "products"], target: "analysis-summary" },
  { label: "匹配推荐商品", names: ["expand", "retrieval", "rerank"], target: "sku-recommendations" },
];
const STATUS: Record<StageStatus, string> = {
  pending: "等待处理", running: "处理中…", ready: "已完成", failed: "未完成，请查看详情",
};

export default function StageRail({ stages, summary, running, onCancel }: Props) {
  return <aside className="stage-rail" aria-label="分析进度">
    <div className="rail-card">
      <div className="rail-head"><h2>分析进度</h2>{running && <button className="btn ghost small" onClick={onCancel}>停止分析</button>}</div>
      <p className="rail-summary" role="status">{summary}</p>
      <ol className="rail-list">{GROUPS.map((group) => {
        const members = stages.filter((stage) => group.names.includes(stage.name));
        const status = groupStatus(members);
        // A completed technical step is not necessarily a visible artifact.
        const target = members.some((stage) => stage.target === group.target) ? group.target : "";
        const body = <><span className="dot" aria-hidden="true">{status === "running" && <i className="spinner" />}</span><span className="rail-text"><strong>{group.label}</strong><small>{STATUS[status]}</small></span></>;
        return <li key={group.label} data-status={status}>{target ? <a href={`#${target}`}>{body}</a> : <div className="rail-plain">{body}</div>}</li>;
      })}</ol>
      <details className="operator-technical"><summary>查看处理详情</summary>
        <ol className="operator-log">{stages.map((stage) => <li key={stage.name}><strong>{stage.label}</strong><small>{stage.note || STATUS[stage.status]}</small></li>)}</ol>
      </details>
    </div>
  </aside>;
}
