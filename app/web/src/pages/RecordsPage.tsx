import type { InterviewRecord } from "@ai-interviewer/shared-types";
import { formatDate } from "../lib/date";

type RecordsPageProps = {
  records: InterviewRecord[];
  onNavigate: (path: string) => void;
};

const statusLabels: Record<InterviewRecord["status"], string> = {
  draft: "準備中",
  in_progress: "回答中",
  submitted: "確認待ち",
  returned: "修正依頼",
  approved: "承認済み",
};

export function RecordsPage({ records, onNavigate }: RecordsPageProps) {
  return (
    <section className="panel page-stack">
      <div className="panel-header">
        <div>
          <h2>記録</h2>
        </div>
        <span className="counter">{records.length}</span>
      </div>

      <div className="table-list">
        <div className="table-row table-head records-workspace-row">
          <span>記録</span>
          <span>対象</span>
          <span>状態</span>
          <span>更新日</span>
          <span>操作</span>
        </div>
        {records.length === 0 ? (
          <p className="empty">回答できる記録はありません。</p>
        ) : records.map((record) => (
          <div className="table-row records-workspace-row" key={record.id}>
            <span>
              <strong>{record.title}</strong>
              <small>{record.knowledgeName}</small>
            </span>
            <span>{record.targetEquipment || record.targetProcess || "-"}</span>
            <span>
              <span className={record.status === "approved" ? "status-pill" : "status-pill muted"}>
                {statusLabels[record.status]}
              </span>
            </span>
            <span>{formatDate(record.updatedAt)}</span>
            <span>
              <button className="primary compact" type="button" onClick={() => onNavigate(`/records/${record.id}`)}>
                詳細
              </button>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
