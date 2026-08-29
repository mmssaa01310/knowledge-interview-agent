import type { InterviewRecord } from "@ai-interviewer/shared-types";
import type { AiProposal } from "../../lib/api";
import { useI18n } from "../../i18n";
import { formatNumber, formatPercent } from "../../lib/date";

type InterviewSectionProps = {
  selectedRecord: InterviewRecord | null;
  proposals: AiProposal[];
  messageText: string;
  onChangeMessage: (value: string) => void;
  onSendMessage: () => void;
  onApproveProposal: (proposalId: string) => void;
};

export function InterviewSection({
  selectedRecord,
  proposals,
  messageText,
  onChangeMessage,
  onSendMessage,
  onApproveProposal
}: InterviewSectionProps) {
  const { t, locale } = useI18n();
  const initialMessages = [
    { role: "AI", text: t("interview.demoMessages.first") },
    { role: "User", text: t("interview.demoMessages.second") },
    { role: "AI", text: t("interview.demoMessages.third") },
  ];
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("navigation.interview")}</p>
          <h2>{t("interview.title")}</h2>
          <p className="lede">
            {t("interview.chatDescription")}
          </p>
        </div>
        <span className="status-pill">{selectedRecord ? selectedRecord.title : t("interview.selectedRecordNone")}</span>
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
              placeholder={t("interview.answerPlaceholder")}
              disabled={!selectedRecord}
            />
            <button className="primary" onClick={onSendMessage} disabled={!selectedRecord || !messageText.trim()}>
              {t("interview.send")}
            </button>
          </div>
        </div>
        <div className="proposal-panel">
          <div className="subheader">
            <strong>{t("interview.proposal.title")}</strong>
            <span className="counter">{formatNumber(proposals.length, locale)}</span>
          </div>
          {proposals.length === 0 ? (
            <p className="empty">{t("interview.proposal.empty")}</p>
          ) : proposals.map((proposal) => (
            <article key={proposal.id} className="proposal-card">
              <div className="proposal-meta">
                <span className="status-pill muted">{t(`interview.proposal.status.${proposal.status}`)}</span>
                <span>{t("interview.proposal.confidence", { value: formatPercent(proposal.confidence, locale) })}</span>
              </div>
              <pre>{JSON.stringify(proposal.structuredData, null, 2)}</pre>
              <div className="actions">
                <button
                  className="primary"
                  onClick={() => onApproveProposal(proposal.id)}
                  disabled={proposal.status === "approved"}
                >
                  {t("interview.proposal.approve")}
                </button>
                <button className="ghost">{t("common.edit")}</button>
                <button className="ghost">{t("interview.proposal.reject")}</button>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
