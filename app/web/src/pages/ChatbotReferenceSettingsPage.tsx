import type { ChatbotLayoutProps } from "../types/pageProps";

export function ChatbotReferenceSettingsPage(props: ChatbotLayoutProps) {
  const selectableDocuments = props.documents.filter((doc) => (
    props.selectedChatbot.referenceKnowledgeDbIds.length === 0
    || props.selectedChatbot.referenceKnowledgeIds.length === 0
    || props.selectedChatbot.referenceKnowledgeIds.includes(doc.knowledgeId)
  ));

  return (
    <section className="panel">
      <div className="panel-header">
        <div><p className="eyebrow">References</p><h2>参照設定</h2></div>
        <button className="ghost" onClick={() => props.navigate(`/chatbots/${props.selectedChatbot.id}/chat`)}>チャットへ戻る</button>
      </div>
      <div className="reference-settings">
        <div>
          <strong>ナレッジDB</strong>
          {props.knowledgeDbs.map((db) => (
            <label className="check-row" key={db.id}>
              <input type="checkbox" checked={props.selectedChatbot.referenceKnowledgeDbIds.includes(db.id)} onChange={(event) => {
                const ids = event.target.checked
                  ? [...props.selectedChatbot.referenceKnowledgeDbIds, db.id]
                  : props.selectedChatbot.referenceKnowledgeDbIds.filter((id) => id !== db.id);
                props.onUpdateReferences({ referenceKnowledgeDbIds: ids });
              }} />
              {db.name}
            </label>
          ))}
        </div>
        <div>
          <strong>ナレッジ</strong>
          {props.knowledges.map((knowledge) => (
            <label className="check-row" key={knowledge.id}>
              <input
                type="checkbox"
                checked={props.selectedChatbot.referenceKnowledgeIds.includes(knowledge.id)}
                onChange={(event) => {
                  const ids = event.target.checked
                    ? [...props.selectedChatbot.referenceKnowledgeIds, knowledge.id]
                    : props.selectedChatbot.referenceKnowledgeIds.filter((id) => id !== knowledge.id);
                  props.onUpdateReferences({ referenceKnowledgeIds: ids });
                }}
              />
              {knowledge.name}
            </label>
          ))}
        </div>
        <div>
          <strong>参照ドキュメント</strong>
          {selectableDocuments.map((doc) => (
            <label className="check-row" key={doc.id}>
              <input type="checkbox" checked={props.selectedChatbot.referenceDocumentIds.includes(doc.id)} onChange={(event) => {
                const ids = event.target.checked
                  ? [...props.selectedChatbot.referenceDocumentIds, doc.id]
                  : props.selectedChatbot.referenceDocumentIds.filter((id) => id !== doc.id);
                props.onUpdateReferences({ referenceDocumentIds: ids });
              }} />
              {doc.fileName}
            </label>
          ))}
        </div>
      </div>

      <div className="reference-settings">
        <div>
          <strong>除外ドキュメント</strong>
          {selectableDocuments.map((doc) => (
            <label className="check-row" key={`${doc.id}-excluded`}>
              <input type="checkbox" checked={props.selectedChatbot.excludedDocumentIds.includes(doc.id)} onChange={(event) => {
                const ids = event.target.checked
                  ? [...props.selectedChatbot.excludedDocumentIds, doc.id]
                  : props.selectedChatbot.excludedDocumentIds.filter((id) => id !== doc.id);
                props.onUpdateReferences({ excludedDocumentIds: ids });
              }} />
              {doc.fileName}
            </label>
          ))}
        </div>
        <div className="form-stack">
          <label>回答モデル<input value={props.selectedChatbot.modelId} onChange={(event) => props.onUpdateReferences({ modelId: event.target.value })} /></label>
          <label>検索件数<input type="number" min={1} max={20} value={props.selectedChatbot.searchLimit} onChange={(event) => props.onUpdateReferences({ searchLimit: Number(event.target.value) || 1 })} /></label>
          <label>信頼度閾値<input type="number" min={0} max={1} step={0.05} value={props.selectedChatbot.confidenceThreshold} onChange={(event) => props.onUpdateReferences({ confidenceThreshold: Number(event.target.value) || 0 })} /></label>
        </div>
      </div>

      <div className="actions">
        <button className="primary" onClick={() => props.navigate(`/chatbots/${props.selectedChatbot.id}/chat`)}>保存してチャットへ戻る</button>
      </div>
    </section>
  );
}
