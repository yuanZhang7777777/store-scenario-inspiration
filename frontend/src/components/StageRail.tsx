import type { StageStatus } from "../api";

/** One row of the pipeline: what ran, how it went, and where its output shows. */
export interface RailStage {
  name: string;
  label: string;
  status: StageStatus;
  note: string;
  /** The section this stage's output appears in, or "" when it produces none. */
  target: string;
}

interface Props {
  stages: RailStage[];
  summary: string;
  running: boolean;
  onCancel: () => void;
}

export default function StageRail({ stages, summary, running, onCancel }: Props) {
  return (
    <aside className="stage-rail" aria-label="分析环节">
      <div className="rail-card">
        <div className="rail-head">
          <h2>分析环节</h2>
          {running && (
            <button className="btn ghost small" onClick={onCancel}>
              停止
            </button>
          )}
        </div>
        <p className="rail-summary">{summary}</p>
        <ol className="rail-list">
          {stages.map((stage) => {
            const body = (
              <>
                <span className="dot" aria-hidden="true">
                  {stage.status === "running" ? <i className="spinner" /> : ""}
                </span>
                <span className="rail-text">
                  <strong>{stage.label}</strong>
                  <small>{stage.note}</small>
                </span>
              </>
            );
            return (
              <li key={stage.name} data-status={stage.status}>
                {stage.target ? (
                  <a href={`#${stage.target}`} title={`跳到「${stage.label}」的结果`}>
                    {body}
                  </a>
                ) : (
                  <div className="rail-plain">{body}</div>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </aside>
  );
}
