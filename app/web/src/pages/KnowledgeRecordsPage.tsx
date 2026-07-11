import { formatDate } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeRecordsPage(props: KnowledgeLayoutProps) {
  const basePath = props.selectedKnowledgeDb && props.selectedKnowledge
    ? `/knowledge-dbs/${props.selectedKnowledgeDb.id}/knowledges/${props.selectedKnowledge.id}`
    : "/knowledge-dbs";

  function getClassificationLabel(record: (typeof props.records)[number]) {
    return record.targetProcess || record.targetEquipment || "未分類";
  }

  function getRecordSummary(record: (typeof props.records)[number]) {
    return record.summary || record.title || "要約未作成";
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Records</p>
          <h2>記録一覧</h2>
        </div>
      </div>
      <div className="inline-form wide">
        <input value={props.newRecordTitle} onChange={(event) => props.setNewRecordTitle(event.target.value)} placeholder="新規記録" />
        <button className="primary" onClick={props.onCreateRecord}>新規記録</button>
        <button className="ghost" onClick={props.onBulkApproveRecords}>選択した記録を一括承認</button>
      </div>
      <div className="table-list">
        <div className="table-row record-detail-row table-head">
          <span>記録内容（要約）</span>
          <span>分類ラベル</span>
          <span>記録者</span>
          <span>作成日</span>
          <span>承認</span>
          <span>操作</span>
        </div>
        {props.records.map((record) => (
          <div className="table-row record-detail-row" key={record.id}>
            <span><strong>{getRecordSummary(record)}</strong></span>
            <span><span className="status-pill muted">{getClassificationLabel(record)}</span></span>
            <span>{record.createdByUserId}</span>
            <span>{formatDate(record.createdAt)}</span>
            <span className="record-approval-cell">
              <span className={record.status === "approved" ? "status-pill" : "status-pill muted"}>{record.status}</span>
              <label className="check-row compact-check">
                <input type="checkbox" checked={props.selectedRecordIds.includes(record.id)} onChange={(event) => {
                  props.setSelectedRecordIds(event.target.checked
                    ? [...props.selectedRecordIds, record.id]
                    : props.selectedRecordIds.filter((id) => id !== record.id));
                }} />
                一括対象
              </label>
            </span>
            <span className="inline-actions">
              <button className="primary compact" onClick={() => props.navigate(`${basePath}/records/${record.id}`)}>詳細</button>
              <button className="danger compact" onClick={() => props.onDeleteRecord(record.id)}>削除</button>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
