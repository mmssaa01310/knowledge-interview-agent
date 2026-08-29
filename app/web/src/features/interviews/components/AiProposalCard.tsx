import type { AiProposal } from "../../../lib/api";
import { useI18n } from "../../../i18n";
import { formatPercent } from "../../../lib/date";

type AiProposalCardProps = {
  proposal: AiProposal;
  onApply: (proposal: AiProposal) => void;
  onApprove: (proposalId: string) => void;
  onReject: (proposalId: string) => void;
  onRemove: (proposalId: string) => void;
};

export function AiProposalCard({ proposal, onApply, onApprove, onReject, onRemove }: AiProposalCardProps) {
  const { t, locale } = useI18n();
  const statusLabel = t(`interview.proposal.status.${proposal.status}`);
  const approvalMethodLabel = proposal.approvalMethod
    ? t(`interview.proposal.approvalMethods.${proposal.approvalMethod}`)
    : "";
  return (
    <article className="proposal-card">
      <div className="proposal-meta">
        <span className="status-pill muted">{statusLabel === `interview.proposal.status.${proposal.status}` ? proposal.status : statusLabel}</span>
        <span>{t("interview.proposal.confidence", { value: formatPercent(proposal.confidence, locale) })}</span>
        {approvalMethodLabel ? <span>{t("interview.proposal.approvalMethod", { method: approvalMethodLabel })}</span> : null}
      </div>
      <div className="proposal-fields">
        {Object.entries(proposal.structuredData).map(([key, value]) => (
          <div className="proposal-field" key={key}>
            <strong>{key}</strong>
            <p>{String(value)}</p>
          </div>
        ))}
      </div>
      <div className="actions">
        <button className="ghost compact" onClick={() => onApply(proposal)}>{t("interview.proposal.apply")}</button>
        <button className="primary compact" onClick={() => onApprove(proposal.id)} disabled={proposal.status === "approved"}>
          {t("interview.proposal.approve")}
        </button>
        <button className="ghost compact" onClick={() => onReject(proposal.id)} disabled={proposal.status === "rejected"}>{t("interview.proposal.reject")}</button>
        <button className="danger compact" onClick={() => onRemove(proposal.id)}>{t("interview.proposal.delete")}</button>
      </div>
    </article>
  );
}
