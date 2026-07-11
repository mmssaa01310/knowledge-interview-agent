import { AiProposalCard } from "../features/interviews/components/AiProposalCard";
import type { AiProposal } from "../lib/api";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function InterviewRecordPage(props: KnowledgeLayoutProps) {
  function applyProposal(proposal: AiProposal) {
    if (proposal.proposalType === "record_summary") {
      const summary = proposal.structuredData.summary;
      if (typeof summary === "string") {
        props.setRecordNotice("要約候補を確認しました");
      }
      return;
    }

    props.setStructuredDraft({
      ...props.structuredDraft,
      ...Object.fromEntries(Object.entries(proposal.structuredData).map(([key, value]) => [key, String(value)]))
    });
    props.setRecordNotice("AI提案を項目へ反映しました");
  }

  return (
    <section className="panel interview-page">
      <div className="panel-header">
        <div>
          <p className="eyebrow">AI Interview Record</p>
          <h2>{props.selectedRecord?.title ?? "記録"}</h2>
          <p className="lede">左で構造化項目を編集し、右でAIチャットと提案カードを確認します。</p>
        </div>
        <div className="actions">
          <span className={props.selectedRecord?.status === "approved" ? "status-pill" : "status-pill muted"}>{props.selectedRecord?.status ?? "draft"}</span>
          <button className="primary" onClick={() => props.setRecordNotice("構造化項目を保存しました")}>保存</button>
          <button className="ghost" onClick={props.onApproveAllForRecord}>全承認</button>
        </div>
      </div>

      <div className="record-columns">
        <aside className="structured-form">
          <div className="rail-title">
            <strong>構造化項目入力</strong>
            <span>{props.sortedFields.length}項目</span>
          </div>
          {props.sortedFields.map((field) => (
            <label key={field.id ?? field.name}>
              <span>{field.name}</span>
              <textarea
                value={props.structuredDraft[field.name] ?? ""}
                onChange={(event) => props.setStructuredDraft({ ...props.structuredDraft, [field.name]: event.target.value })}
                placeholder={field.description ?? "ヒアリング内容を入力"}
              />
              <div className="toolbar">
                <span className="status-pill muted">{field.required ? "必須" : "任意"}</span>
                <span className="status-pill muted">{field.askByAi ? "AI質問対象" : "手入力"}</span>
              </div>
            </label>
          ))}
          {props.recordNotice && <span className="notice">{props.recordNotice}</span>}
        </aside>

        <div className="proposal-panel">
          <div className="stream-banner">
            <strong>SSEストリーム</strong>
            <span>stream_start / delta / stream_end / proposal_created の順で更新します。</span>
          </div>
          <div className="chat-log">
            {props.interviewMessages.map((message, index) => (
              <div key={index} className={`bubble ${message.role === "ai" ? "ai" : "user"}`}>
                <p>{message.text}</p>
              </div>
            ))}
          </div>
          <div className="message-box">
            <textarea value={props.chatInput} onChange={(event) => props.setChatInput(event.target.value)} placeholder="回答を入力" />
            <div className="actions">
              <button className="primary" onClick={props.onSendInterviewMessage}>送信</button>
              <button className="ghost">音声入力</button>
            </div>
          </div>

          <div className="panel-header compact-header">
            <div>
              <strong>AI提案カード</strong>
              <p className="lede">承認前の提案だけを確認し、必要に応じて修正して反映します。</p>
            </div>
            <button className="ghost compact" onClick={props.onApproveAllForRecord}>全承認</button>
          </div>
          <div className="proposal-list">
            {props.proposals.length === 0 ? <p className="empty">AI提案はまだありません。</p> : props.proposals.map((proposal) => (
              <AiProposalCard
                key={proposal.id}
                proposal={proposal}
                onApply={applyProposal}
                onApprove={props.onApproveOne}
                onReject={props.onRejectProposal}
                onRemove={props.onRemoveProposal}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
