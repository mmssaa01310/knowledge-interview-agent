import type { InterviewRecord } from "@ai-interviewer/shared-types";
import type { AiProposal } from "../../lib/api";

type InterviewSectionProps = {
  selectedRecord: InterviewRecord | null;
  proposals: AiProposal[];
  messageText: string;
  onChangeMessage: (value: string) => void;
  onSendMessage: () => void;
  onApproveProposal: (proposalId: string) => void;
};

const initialMessages = [
  { role: "AI", text: "まず、今回整理したい内容を教えてください。" },
  { role: "User", text: "朝一と段取り替え直後に起きやすい事象を整理したいです。" },
  { role: "AI", text: "ありがとうございます。状況を確認しながら、構造化提案の下書きを作成します。" }
];

export function InterviewSection({
  selectedRecord,
  proposals,
  messageText,
  onChangeMessage,
  onSendMessage,
  onApproveProposal
}: InterviewSectionProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Interview</p>
          <h2>AIインタビュー</h2>
          <p className="lede">
            会話で不足情報を聞き取り、AI提案は承認前の下書きとして保存します。
          </p>
        </div>
        <span className="status-pill">{selectedRecord ? selectedRecord.title : "記録未選択"}</span>
      </div>
      <div className="interview-layout">
        <div className="chat-panel">
          <div className="chat-log">
            {initialMessages.map((message, index) => (
              <div key={index} className={`bubble ${message.role === "AI" ? "ai" : "user"}`}>
                <p>{message.text}</p>
              </div>
            ))}
          </div>
          <div className="message-box">
            <textarea
              value={messageText}
              onChange={(event) => onChangeMessage(event.target.value)}
              placeholder="熟練者の回答や追加情報を入力"
              disabled={!selectedRecord}
            />
            <button className="primary" onClick={onSendMessage} disabled={!selectedRecord || !messageText.trim()}>
              送信して提案生成
            </button>
          </div>
        </div>
        <div className="proposal-panel">
          <div className="subheader">
            <strong>構造化提案</strong>
            <span className="counter">{proposals.length}</span>
          </div>
          {proposals.length === 0 ? (
            <p className="empty">選択中の記録にはAI提案がありません。</p>
          ) : proposals.map((proposal) => (
            <article key={proposal.id} className="proposal-card">
              <div className="proposal-meta">
                <span className="status-pill muted">{proposal.status}</span>
                <span>信頼度 {Math.round(proposal.confidence * 100)}%</span>
              </div>
              <pre>{JSON.stringify(proposal.structuredData, null, 2)}</pre>
              <div className="actions">
                <button
                  className="primary"
                  onClick={() => onApproveProposal(proposal.id)}
                  disabled={proposal.status === "approved"}
                >
                  個別承認
                </button>
                <button className="ghost">修正</button>
                <button className="ghost">差し戻し</button>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
