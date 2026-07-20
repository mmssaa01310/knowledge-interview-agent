import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeOverviewPage({
  selectedKnowledgeDb,
  selectedKnowledge,
  records,
  documents,
  sortedFields,
  overviewSummaryDraft,
  setOverviewSummaryDraft,
  isGeneratingOverviewSummary,
  recordNotice,
  navigate,
  onGenerateOverviewSummary,
  onSaveOverviewSummary,
  onRevertOverviewSummary
}: KnowledgeLayoutProps) {
  if (!selectedKnowledgeDb || !selectedKnowledge) return null;
  const basePath = `/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${selectedKnowledge.id}`;
  const recordSummaries = records
    .filter((record) => record.summary?.trim())
    .slice(0, 3);

  return (
    <section className="panel page-stack">
      <div className="panel-header">
        <div>
          <h2>{selectedKnowledge.name}</h2>
          {selectedKnowledge.description && <p className="lede">{selectedKnowledge.description}</p>}
        </div>
      </div>

      <div className="overview-action-grid">
        <button className="overview-action-card" onClick={() => navigate(`${basePath}/records`)}>
          <strong>記録</strong>
          <span>{records.length}件</span>
        </button>
        <button className="overview-action-card" onClick={() => navigate(`${basePath}/settings`)}>
          <strong>質問設定</strong>
          <span>{sortedFields.length}問</span>
        </button>
        <button className="overview-action-card" onClick={() => navigate(`${basePath}/documents`)}>
          <strong>ドキュメント</strong>
          <span>{documents.length}件</span>
        </button>
      </div>

      <section className="settings-section">
        <div className="section-title-row compact-row">
          <div>
            <h3>記録要約</h3>
            <p>記録済み内容から作成された要約情報</p>
          </div>
          <button className="ghost compact" onClick={() => navigate(`${basePath}/records`)}>記録一覧</button>
        </div>
        <label className="summary-editor">
          <span>概要に表示する記録要約</span>
          <textarea
            value={overviewSummaryDraft}
            onChange={(event) => setOverviewSummaryDraft(event.target.value)}
            placeholder="このナレッジに蓄積された記録の要約を入力"
          />
          <div className="toolbar">
            <button
              type="button"
              className="ghost compact"
              onClick={onGenerateOverviewSummary}
              disabled={isGeneratingOverviewSummary || records.length === 0}
            >
              {isGeneratingOverviewSummary ? "生成中" : "AIで要約"}
            </button>
            <button type="button" className="primary compact" onClick={onSaveOverviewSummary}>保存</button>
            <button type="button" className="ghost compact" onClick={onRevertOverviewSummary}>元に戻す</button>
          </div>
        </label>
        {recordNotice ? <p className="notice">{recordNotice}</p> : null}
        <div className="summary-list">
          {records.length === 0 ? (
            <p className="empty compact-empty">記録がまだありません。</p>
          ) : recordSummaries.length === 0 ? (
            <p className="empty compact-empty">個別記録の要約はまだありません。</p>
          ) : recordSummaries.map((record) => (
            <button
              type="button"
              className="summary-card"
              key={record.id}
              onClick={() => navigate(`${basePath}/records/${record.id}`)}
            >
              <strong>{record.title}</strong>
              <span>{record.summary}</span>
            </button>
          ))}
        </div>
      </section>
    </section>
  );
}
