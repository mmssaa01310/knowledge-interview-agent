import type { Chatbot } from "../types/app";

type ChatbotListPageProps = {
  chatbots: Chatbot[];
  onNavigate: (path: string) => void;
};

export function ChatbotListPage({ chatbots, onNavigate }: ChatbotListPageProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Chatbots</p>
          <h2>チャットボット一覧</h2>
          <p className="lede">左の一覧からチャットボットを選択し、チャットまたは参照設定へ進みます。</p>
        </div>
      </div>
      <div className="table-list">
        <div className="table-row table-head"><span>名前</span><span>参照DB</span><span>参照ナレッジ</span><span>参照文書</span><span>モデル</span></div>
        {chatbots.length === 0 ? (
          <p className="empty">チャットボットがありません。左の「+ 新規」から作成してください。</p>
        ) : chatbots.map((chatbot) => (
          <button type="button" key={chatbot.id} className="table-row selectable" onClick={() => onNavigate(`/chatbots/${chatbot.id}`)}>
            <span><strong>{chatbot.name}</strong></span>
            <span>{chatbot.referenceKnowledgeDbIds.length}</span>
            <span>{chatbot.referenceKnowledgeIds.length}</span>
            <span>{chatbot.referenceDocumentIds.length}</span>
            <span>{chatbot.modelId}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
