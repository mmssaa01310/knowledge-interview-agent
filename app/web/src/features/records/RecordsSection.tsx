import type { InterviewRecord } from "@ai-interviewer/shared-types";

type RecordsSectionProps = {
  records: InterviewRecord[];
  selectedRecordId: string | null;
  onSelectRecord: (recordId: string) => void;
  onApproveAll: (recordId: string) => void;
};

export function RecordsSection({ records, selectedRecordId, onSelectRecord, onApproveAll }: RecordsSectionProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Records</p>
          <h2>記録一覧</h2>
          <p className="lede">AIインタビューから作成された構造化記録と承認状態を確認します。</p>
        </div>
        <span className="counter">{records.length}</span>
      </div>
      <div className="table-list">
        <div className="table-row table-head record-row">
          <span>記録名</span>
          <span>対象</span>
          <span>承認状況</span>
          <span>操作</span>
        </div>
        {records.length === 0 ? (
          <p className="empty">記録はまだありません。</p>
        ) : records.map((record) => (
          <div
            key={record.id}
            className={record.id === selectedRecordId ? "table-row record-row selected" : "table-row record-row"}
          >
            <button className="text-cell" onClick={() => onSelectRecord(record.id)}>
              <strong>{record.title}</strong>
              <small>{record.summary ?? "要約は未作成です"}</small>
            </button>
            <span>{record.targetEquipment ?? "-"} / {record.targetProcess ?? "-"}</span>
            <span>
              <span className="status-pill muted">{record.status}</span>
              <small>承認 {record.approvedFieldCount} / 未承認 {record.unapprovedFieldCount}</small>
            </span>
            <span className="inline-actions">
              <button className="ghost compact" onClick={() => onSelectRecord(record.id)}>詳細</button>
              <button className="primary compact" onClick={() => onApproveAll(record.id)}>全承認</button>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
