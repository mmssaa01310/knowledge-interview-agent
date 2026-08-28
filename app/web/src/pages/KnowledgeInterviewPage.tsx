import { formatDate } from "../lib/date";
import {
  isInterviewConfigurationComplete,
} from "../features/interviews/interviewConfiguration";
import type { KnowledgeLayoutProps } from "../types/pageProps";

const resumableStatuses = new Set(["draft", "in_progress", "returned"]);

const statusLabels: Record<string, string> = {
  draft: "準備中",
  in_progress: "回答中",
  returned: "修正依頼",
};

export function KnowledgeInterviewPage(props: KnowledgeLayoutProps) {
  const { selectedKnowledgeDb, selectedKnowledge } = props;
  if (!selectedKnowledgeDb || !selectedKnowledge) return null;

  const isConfigured = isInterviewConfigurationComplete(selectedKnowledge);
  const basePath = `/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${selectedKnowledge.id}`;
  const resumableRecords = props.records.filter((record) => resumableStatuses.has(record.status));

  function openRecord(recordId: string) {
    props.navigate(`${basePath}/records/${recordId}`);
  }

  return (
    <section className="panel page-stack interview-launch-page">
      <div className="panel-header interview-launch-header">
        <div>
          <h2>インタビュー</h2>
        </div>
        <span className={isConfigured ? "status-pill" : "status-pill muted"}>
          {isConfigured ? "開始できます" : "設定が必要です"}
        </span>
      </div>

      {!isConfigured ? (
        <div className="interview-launch-notice">
          <div>
            <strong>インタビューを開始する前に設定してください</strong>
            <p>用途と実行モデルを選択して保存します。</p>
          </div>
          <button className="primary" type="button" onClick={() => props.navigate(`${basePath}/settings`)}>
            設定を開始
          </button>
        </div>
      ) : null}

      {isConfigured ? (
        <section className="interview-launch-card">
          <div className="new-interview-form">
            <label className="sr-only" htmlFor="new-record-title">記録タイトル</label>
            <input
              id="new-record-title"
              value={props.newRecordTitle}
              onChange={(event) => props.setNewRecordTitle(event.target.value)}
              placeholder="記録タイトル（任意）"
            />
            <button type="button" className="primary" onClick={props.onCreateRecord}>
              新規インタビューを開始
            </button>
          </div>
          {props.recordNotice ? <p className="notice" role="status">{props.recordNotice}</p> : null}
        </section>
      ) : null}

      {resumableRecords.length > 0 ? (
        <section className="interview-resume-section">
          <div className="section-title-row compact-row">
            <div>
              <h3>途中のインタビュー</h3>
            </div>
            <span className="counter">{resumableRecords.length}</span>
          </div>
          <div className="interview-resume-list">
            {resumableRecords.map((record) => (
              <div className="interview-resume-card" key={record.id}>
                <div className="interview-resume-card-main">
                  <strong>{record.title}</strong>
                  <span>{record.targetEquipment || record.targetProcess || selectedKnowledge.name}</span>
                  <small>最終更新 {formatDate(record.updatedAt)}</small>
                </div>
                <div className="interview-resume-card-actions">
                  <span className={record.status === "in_progress" ? "status-pill active" : "status-pill muted"}>
                    {statusLabels[record.status]}
                  </span>
                  <button className="ghost compact" type="button" onClick={() => openRecord(record.id)}>
                    {record.status === "draft" ? "開く" : "再開"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

    </section>
  );
}
