import type { ChatbotLayoutProps } from "../types/pageProps";

export function ChatbotChatPage(props: ChatbotLayoutProps) {
  const referencedKnowledgeDbs = props.knowledgeDbs.filter((db) => props.selectedChatbot.referenceKnowledgeDbIds.includes(db.id));
  const referencedKnowledges = props.knowledges.filter((knowledge) => props.selectedChatbot.referenceKnowledgeIds.includes(knowledge.id));
  const referencedDocuments = props.documents.filter((doc) => props.selectedChatbot.referenceDocumentIds.includes(doc.id));
  const excludedDocuments = props.documents.filter((doc) => props.selectedChatbot.excludedDocumentIds.includes(doc.id));

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Reference Chat</p>
          <h2>{props.selectedChatbot.name}</h2>
          <p className="lede">承認済みナレッジと取り込み完了ドキュメントだけを根拠として回答します。</p>
        </div>
        <button className="ghost" onClick={() => props.navigate(`/chatbots/${props.selectedChatbot.id}/references`)}>参照設定</button>
      </div>
      <div className="reference-chat-layout">
        <div className="proposal-panel">
          <div className="toolbar">
            <span className="status-pill">対象DB {referencedKnowledgeDbs.length}</span>
            <span className="status-pill">対象ナレッジ {referencedKnowledges.length}</span>
            <span className="status-pill">完了文書 {referencedDocuments.filter((doc) => doc.ingestionStatus === "completed").length}</span>
            <span className="status-pill muted">除外文書 {excludedDocuments.length}</span>
          </div>

          <div className="chat-log">
            {props.chatbotMessages.map((message, index) => (
              <div key={index} className={`bubble ${message.role === "ai" ? "ai" : "user"}`}>
                <p>{message.text}</p>
                {message.evidences?.length ? (
                  <div className="message-evidence-list">
                    {message.evidences.map((evidence, evidenceIndex) => (
                      <div className="message-evidence" key={`${evidence.title}-${evidenceIndex}`}>
                        <strong>{evidence.type === "knowledge" ? "根拠ナレッジ" : "根拠ドキュメント"}</strong>
                        <span>{evidence.title}</span>
                        <small>{evidence.detail}</small>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          <div className="message-box">
            <textarea value={props.chatbotInput} onChange={(event) => props.setChatbotInput(event.target.value)} placeholder="承認済みナレッジに質問" />
            <div className="actions"><button className="primary" onClick={props.onSendChatbotMessage}>送信</button><button className="ghost">音声入力</button></div>
          </div>
        </div>

        <aside className="reference-sidebar">
          <div className="ai-assist">
            <strong>検索ポリシー</strong>
            <span>モデル: {props.selectedChatbot.modelId || "未設定"}</span>
            <span>検索件数: {props.selectedChatbot.searchLimit}</span>
            <span>信頼度閾値: {Math.round(props.selectedChatbot.confidenceThreshold * 100)}%</span>
            <span>未承認ナレッジは検索対象外です。</span>
          </div>
          <div className="ai-assist">
            <strong>参照ナレッジDB</strong>
            {referencedKnowledgeDbs.length === 0 ? <span>参照先が未設定です。</span> : referencedKnowledgeDbs.map((db) => (
              <span key={db.id}>{db.name}</span>
            ))}
          </div>
          <div className="ai-assist">
            <strong>参照ナレッジ</strong>
            {referencedKnowledges.length === 0 ? <span>参照ナレッジが未設定です。</span> : referencedKnowledges.map((knowledge) => (
              <span key={knowledge.id}>{knowledge.name}</span>
            ))}
          </div>
          <div className="ai-assist">
            <strong>参照ドキュメント</strong>
            {referencedDocuments.length === 0 ? <span>参照文書が未設定です。</span> : referencedDocuments.map((doc) => (
              <span key={doc.id}>{doc.fileName} / {doc.ingestionStatus}</span>
            ))}
          </div>
        </aside>
      </div>
    </section>
  );
}
