import type { AiProposal } from "../../../lib/api";

type AiProposalCardProps = {
  proposal: AiProposal;
  onApply: (proposal: AiProposal) => void;
  onApprove: (proposalId: string) => void;
  onReject: (proposalId: string) => void;
  onRemove: (proposalId: string) => void;
};

export function AiProposalCard({ proposal, onApply, onApprove, onReject, onRemove }: AiProposalCardProps) {
  return (
    <article className="proposal-card">
      <div className="proposal-meta">
        <span className="status-pill muted">{proposal.status}</span>
        <span>信頼度 {Math.round(proposal.confidence * 100)}%</span>
        {proposal.approvalMethod ? <span>承認方式 {proposal.approvalMethod}</span> : null}
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
        <button className="ghost compact" onClick={() => onApply(proposal)}>修正して反映</button>
        <button className="primary compact" onClick={() => onApprove(proposal.id)} disabled={proposal.status === "approved"}>
          個別承認
        </button>
        <button className="ghost compact" onClick={() => onReject(proposal.id)} disabled={proposal.status === "rejected"}>差し戻し</button>
        <button className="danger compact" onClick={() => onRemove(proposal.id)}>削除</button>
      </div>
    </article>
  );
}
