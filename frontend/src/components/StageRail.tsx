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
  { label: "店铺分析", names: ["recognize", "clues", "synthesis"], target: "analysis-summary" },
  { label: "生成场景", names: ["scenes"], target: "analysis-scenes" },
  { label: "场景商品", names: ["products"], target: "sku-recommendations" },
  { label: "匹配商品", names: ["expand", "retrieval", "rerank"], target: "sku-recommendations" },
];
const STATUS: Record<StageStatus, string> = {
  pending: "等待生成", running: "正在生成", ready: "已完成", failed: "未完成",
};

export default function StageRail({ stages, summary, running, onCancel }: Props) {
  return <section className="stage-rail" aria-label="生成进度" data-running={running}>
    <div className="rail-card">
      <div className="rail-head"><p className="rail-summary" role="status">{summary}</p>{running && <button className="btn ghost small" onClick={onCancel}>停止生成</button>}</div>
      <ol className="rail-list">{GROUPS.map((group, index) => {
        const members = stages.filter((stage) => group.names.includes(stage.name));
        const status = groupStatus(members);
        // A completed technical step is not necessarily a visible artifact.
        const target = members.some((stage) => stage.target === group.target) ? group.target : "";
        const body = <><span className="dot" aria-hidden="true">{status === "ready" ? "✓" : index + 1}</span><span className="rail-text"><strong>{group.label}</strong><small>{STATUS[status]}</small></span></>;
        return <li key={group.label} data-status={status} aria-current={status === "running" ? "step" : undefined}>{target ? <a href={`#${target}`}>{body}</a> : <div className="rail-plain">{body}</div>}</li>;
      })}</ol>
    </div>
  </section>;
}
