import type { InterviewRecord } from "@ai-interviewer/shared-types";
import { formatDate } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";

const statusLabels: Record<InterviewRecord["status"], string> = {
  draft: "準備中",
  in_progress: "回答中",
  submitted: "確認待ち",
  returned: "修正依頼",
  approved: "承認済み",
};

export function KnowledgeRecordsPage(props: KnowledgeLayoutProps) {
  const { selectedKnowledgeDb, selectedKnowledge } = props;
  if (!selectedKnowledgeDb || !selectedKnowledge) return null;

  const basePath = `/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${selectedKnowledge.id}`;

  function openRecord(recordId: string) {
    props.navigate(`${basePath}/records/${recordId}`);
  }

  function returnRecord(recordId: string) {
    const reviewNote = window.prompt("修正してほしい内容を入力してください。", "");
    if (reviewNote?.trim()) {
      void props.onChangeRecordStatusForRecord(recordId, "returned", reviewNote.trim());
    }
  }

  function approveRecord(recordId: string) {
    if (window.confirm("この記録を承認しますか？")) {
      void props.onChangeRecordStatusForRecord(recordId, "approved");
    }
  }

  return (
    <section className="panel page-stack">
      <div className="panel-header">
        <div>
          <h2>記録</h2>
        </div>
        <button className="primary" type="button" onClick={() => props.navigate(`${basePath}/interview`)}>
          新しい記録
        </button>
      </div>

      {props.recordNotice ? <p className="notice" role="status">{props.recordNotice}</p> : null}

      <div className="table-list">
        <div className="table-row table-head records-workspace-row knowledge-records-row">
          <span>記録</span>
          <span>担当者</span>
          <span>状態</span>
          <span>更新日</span>
          <span>操作</span>
        </div>
        {props.records.length === 0 ? (
          <p className="empty">記録はありません。</p>
        ) : props.records.map((record) => (
          <div className="table-row records-workspace-row knowledge-records-row" key={record.id}>
            <span>
              <strong>{record.title}</strong>
              <small>{record.targetEquipment || record.targetProcess || "-"}</small>
            </span>
            <span>{record.ownerUserId || "未設定"}</span>
            <span>
              <span className={record.status === "approved" ? "status-pill" : "status-pill muted"}>
                {statusLabels[record.status]}
              </span>
            </span>
            <span>{formatDate(record.updatedAt)}</span>
            <span className="inline-actions">
              <button className="ghost compact" type="button" onClick={() => openRecord(record.id)}>
                詳細
              </button>
              {record.status === "draft" ? (
                <button className="primary compact" type="button" onClick={() => void props.onChangeRecordStatusForRecord(record.id, "in_progress")}>
                  公開
                </button>
              ) : null}
              {record.status === "submitted" ? (
                <>
                  <button className="ghost compact" type="button" onClick={() => returnRecord(record.id)}>
                    差し戻す
                  </button>
                  <button className="primary compact" type="button" onClick={() => approveRecord(record.id)}>
                    承認
                  </button>
                </>
              ) : null}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
