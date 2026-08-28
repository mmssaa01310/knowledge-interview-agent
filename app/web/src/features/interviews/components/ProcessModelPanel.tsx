import { useMemo, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
  Position
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { InterviewState } from "../../../types/app";

type ProcessModelPanelProps = {
  interviewState: InterviewState | null;
};

function text(value: unknown, fallback: string) {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function number(value: unknown, fallback: number) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function buildFlowchart(processState: NonNullable<InterviewState["processState"]>) {
  const nodes = (processState.nodes ?? [])
    .filter((node) => node.lifecycle !== "superseded")
    .map((node, index): Node => {
      const isCandidate = node.confirmationStatus !== "confirmed";
      return {
        id: text(node.nodeId, `node-${index}`),
        position: { x: (index % 3) * 230, y: Math.floor(index / 3) * 110 },
        data: { label: text(node.label, "未命名処理") },
        sourcePosition: Position.Bottom,
        targetPosition: Position.Top,
        style: {
          borderRadius: 10,
          border: `${isCandidate ? "1px dashed" : "1px solid"} #7fa6cf`,
          background: node.nodeType === "decision" ? "#fff8e8" : "#ffffff",
          color: "#26394d",
          padding: "10px 12px",
          width: 190,
          fontSize: 13,
          boxShadow: "0 2px 8px rgba(31, 45, 61, 0.08)"
        }
      };
    });
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = (processState.edges ?? [])
    .filter((edge) => edge.lifecycle !== "superseded")
    .filter((edge) => nodeIds.has(text(edge.sourceNodeId, "")) && nodeIds.has(text(edge.targetNodeId, "")))
    .map((edge, index): Edge => ({
      id: text(edge.edgeId, `edge-${index}`),
      source: text(edge.sourceNodeId, ""),
      target: text(edge.targetNodeId, ""),
      label: text(edge.label ?? edge.condition, "") || undefined,
      type: "smoothstep",
      animated: false,
      style: edge.confirmationStatus === "confirmed" ? undefined : { strokeDasharray: "5 4" }
    }));
  return { nodes, edges };
}

function buildSequence(processState: NonNullable<InterviewState["processState"]>) {
  const participants = (processState.participants ?? []).filter(
    (participant) => participant.lifecycle !== "superseded",
  );
  const nodes = participants.map((participant, index): Node => {
    const isCandidate = participant.confirmationStatus !== "confirmed";
    return {
      id: text(participant.participantId, `participant-${index}`),
      position: { x: index * 210, y: 20 },
      data: { label: text(participant.name, "関係者") },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      style: {
        borderRadius: 10,
        border: `${isCandidate ? "1px dashed" : "1px solid"} #9c86c4`,
        background: "#faf7ff",
        color: "#392b53",
        padding: "10px 12px",
        width: 170,
        fontSize: 13
      }
    };
  });
  const participantIds = new Set(nodes.map((node) => node.id));
  const edges = [...(processState.interactions ?? [])]
    .sort((left, right) => number(left.sequence, 0) - number(right.sequence, 0))
    .filter((interaction) => interaction.lifecycle !== "superseded")
    .filter((interaction) => Array.isArray(interaction.evidenceTranscriptIds) && interaction.evidenceTranscriptIds.length > 0)
    .filter((interaction) => participantIds.has(text(interaction.sourceParticipantId, "")) && participantIds.has(text(interaction.targetParticipantId, "")))
    .map((interaction, index): Edge => ({
      id: text(interaction.interactionId, `interaction-${index}`),
      source: text(interaction.sourceParticipantId, ""),
      target: text(interaction.targetParticipantId, ""),
      label: text(interaction.action, "やり取り"),
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
      style: interaction.confirmationStatus === "confirmed" ? undefined : { strokeDasharray: "5 4" }
    }));
  return { nodes, edges };
}

export function ProcessModelPanel({ interviewState }: ProcessModelPanelProps) {
  const [view, setView] = useState<"flowchart" | "sequence">("flowchart");
  const processState = interviewState?.processState;
  const flowchart = useMemo(() => buildFlowchart(processState ?? {}), [processState]);
  const sequence = useMemo(() => buildSequence(processState ?? {}), [processState]);
  const isStructuredProfile = interviewState?.interviewProfile === "business_process"
    || interviewState?.interviewProfile === "system_requirement";
  const processStatus = interviewState?.interviewProfile === "business_process"
    ? "present"
    : interviewState?.applicabilityState?.process?.status ?? "unknown";
  if (!isStructuredProfile) return null;

  const activeGraph = view === "flowchart" ? flowchart : sequence;
  const graphReady = view === "flowchart"
    ? flowchart.nodes.length >= 2 && flowchart.edges.length >= 1
    : sequence.nodes.length >= 2 && sequence.edges.length >= 1;

  return (
    <section className="process-model-panel" aria-label="業務フローとシーケンス図">
      {processStatus === "unknown" ? (
        <p className="empty process-model-empty">
          この要望に業務フローがあるか確認しています。
        </p>
      ) : processStatus === "not_applicable" ? (
        <p className="empty process-model-empty">
          この要望は、処理フローなしで要件を整理します。
        </p>
      ) : <>
      <div className="process-model-header">
        <div>
          <strong>処理の流れ</strong>
          <p>会話から整理した内容です。点線の要素は確認前です。</p>
        </div>
        <span className="status-pill muted">
          会話から整理
        </span>
      </div>
      <div className="process-model-tabs" role="tablist" aria-label="処理モデル表示切替">
        <button type="button" role="tab" aria-selected={view === "flowchart"} className={view === "flowchart" ? "active" : ""} onClick={() => setView("flowchart")}>フローチャート</button>
        <button type="button" role="tab" aria-selected={view === "sequence"} className={view === "sequence" ? "active" : ""} onClick={() => setView("sequence")}>シーケンス図</button>
      </div>
      {graphReady ? (
        <div className="process-flow-canvas">
          <ReactFlow
            key={view}
            nodes={activeGraph.nodes}
            edges={activeGraph.edges}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            zoomOnDoubleClick={false}
            panOnDrag
          >
            <Background gap={20} size={1} color="#dfe8f0" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      ) : (
        <p className="empty process-model-empty">
          {view === "flowchart"
            ? "処理の流れを整理しています。情報が集まると、ここにフローチャートが表示されます。"
            : "関係者間のやり取りを整理しています。情報が集まると、ここにシーケンス図が表示されます。"}
        </p>
      )}
      {view === "sequence" && sequence.edges.length > 0 ? (
        <ol className="sequence-interaction-list">
          {[...(processState?.interactions ?? [])]
            .filter((interaction) => interaction.lifecycle !== "superseded")
            .filter((interaction) => Array.isArray(interaction.evidenceTranscriptIds) && interaction.evidenceTranscriptIds.length > 0)
            .sort((left, right) => number(left.sequence, 0) - number(right.sequence, 0))
            .map((interaction, index) => (
              <li key={text(interaction.interactionId, `interaction-${index}`)}>
                <span>{number(interaction.sequence, index + 1)}</span>
                {text(interaction.action, "やり取り")}
                {interaction.data ? `（${text(interaction.data, "")}）` : ""}
              </li>
            ))}
        </ol>
      ) : null}
      </>}
    </section>
  );
}
