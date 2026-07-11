import type { ChatbotLayoutProps } from "../types/pageProps";

export function ChatbotOverviewPage({ selectedChatbot, navigate }: ChatbotLayoutProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Overview</p>
          <h2>{selectedChatbot.name}</h2>
          <p className="lede">参照対象を設定し、承認済みナレッジと文書を根拠に回答します。</p>
        </div>
      </div>
      <div className="flow-grid">
        <button onClick={() => navigate(`/chatbots/${selectedChatbot.id}/chat`)}>
          <strong>チャット</strong><span>ナレッジ参照QAを確認</span>
        </button>
        <button onClick={() => navigate(`/chatbots/${selectedChatbot.id}/references`)}>
          <strong>参照設定</strong><span>参照DBと文書を選択</span>
        </button>
      </div>
    </section>
  );
}
