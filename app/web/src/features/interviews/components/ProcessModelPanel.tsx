import { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
  Position,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ApiError } from "../../../lib/api";
import type { InterviewState, ProcessModelState } from "../../../types/app";

type ProcessCollection = "participants" | "nodes" | "edges" | "interactions";
type ProcessEntity = Record<string, unknown>;
type Graph = { nodes: Node[]; edges: Edge[] };
type CommandMessage = { role: "user" | "assistant" | "error"; text: string };
type FlowchartNodeData = { label?: string; nodeType?: string; candidate?: boolean };
type FlowchartNodeType = Node<FlowchartNodeData>;

type ProcessModelPanelProps = {
  interviewState: InterviewState | null;
  canEdit?: boolean;
  onSaveProcessModel: (
    processState: ProcessModelState,
    baseProcessVersion: number,
    baseStateVersion: number,
  ) => Promise<InterviewState>;
  onEditProcessModel: (
    instruction: string,
    baseProcessVersion: number,
    baseStateVersion: number,
  ) => Promise<{ interviewState: InterviewState; reply: string }>;
};

function text(value: unknown, fallback: string) {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function number(value: unknown, fallback: number) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function cloneProcessState(processState: ProcessModelState): ProcessModelState {
  return JSON.parse(JSON.stringify(processState)) as ProcessModelState;
}

function processVersion(
  processState: ProcessModelState,
  interviewState: InterviewState | null,
) {
  return number(processState.version, number(interviewState?.processVersion, 0));
}

function flowchartShape(nodeType: string) {
  if (nodeType === "start" || nodeType === "end") return "terminator";
  if (nodeType === "decision") return "decision";
  if (nodeType === "data") return "data";
  if (nodeType === "subprocess") return "subprocess";
  if (nodeType === "system") return "system";
  return "process";
}

function FlowchartNode({ data }: NodeProps<FlowchartNodeType>) {
  const shape = flowchartShape(text(data.nodeType, "activity"));
  return (
    <div className={`flowchart-node flowchart-node-${shape}${data.candidate ? " is-candidate" : ""}`}>
      <Handle type="target" position={Position.Top} />
      <span>{text(data.label, "未命名処理")}</span>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const flowchartNodeTypes: NodeTypes = { flowchart: FlowchartNode };

function buildFlowchart(processState: ProcessModelState): Graph {
  const nodes = (processState.nodes ?? [])
    .filter((node) => node.lifecycle !== "superseded")
    .map((node, index): Node => {
      const isCandidate = node.confirmationStatus !== "confirmed";
      const nodeType = text(node.nodeType, "activity");
      return {
        id: text(node.nodeId, `node-${index}`),
        type: "flowchart",
        position: { x: (index % 3) * 230, y: Math.floor(index / 3) * 110 },
        data: { label: text(node.label, "未命名処理"), nodeType, candidate: isCandidate },
        sourcePosition: Position.Bottom,
        targetPosition: Position.Top,
        style: {
          width: nodeType === "decision" ? 170 : 190,
          height: nodeType === "decision" ? 116 : 64,
          padding: 0,
          background: "transparent",
          border: "none",
          boxShadow: "none",
        },
        className: isCandidate ? "is-candidate" : undefined,
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
      markerEnd: { type: MarkerType.ArrowClosed },
      style: edge.confirmationStatus === "confirmed" ? undefined : { strokeDasharray: "5 4" },
    }));
  return { nodes, edges };
}

function buildSequence(processState: ProcessModelState): Graph {
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
        fontSize: 13,
      },
    };
  });
  const participantIds = new Set(nodes.map((node) => node.id));
  const edges = [...(processState.interactions ?? [])]
    .sort((left, right) => number(left.sequence, 0) - number(right.sequence, 0))
    .filter((interaction) => interaction.lifecycle !== "superseded")
    .filter((interaction) => (
      interaction.confirmationStatus === "confirmed"
      || (Array.isArray(interaction.evidenceTranscriptIds) && interaction.evidenceTranscriptIds.length > 0)
    ))
    .filter((interaction) => participantIds.has(text(interaction.sourceParticipantId, "")) && participantIds.has(text(interaction.targetParticipantId, "")))
    .map((interaction, index): Edge => ({
      id: text(interaction.interactionId, `interaction-${index}`),
      source: text(interaction.sourceParticipantId, ""),
      target: text(interaction.targetParticipantId, ""),
      label: text(interaction.action, "やり取り"),
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
      style: interaction.confirmationStatus === "confirmed" ? undefined : { strokeDasharray: "5 4" },
    }));
  return { nodes, edges };
}

function SequenceDiagram({ graph }: { graph: Graph }) {
  const participants = graph.nodes;
  const messages = graph.edges;
  const marginX = 100;
  const columnGap = 220;
  const headerY = 18;
  const headerHeight = 44;
  const messageStartY = 106;
  const messageRowHeight = 62;
  const width = Math.max(560, marginX * 2 + Math.max(participants.length - 1, 0) * columnGap);
  const height = Math.max(320, messageStartY + messages.length * messageRowHeight + 42);
  const participantPositions = new Map(
    participants.map((participant, index) => [participant.id, marginX + index * columnGap]),
  );

  return (
    <div className="sequence-diagram-scroll" role="img" aria-label="UMLシーケンス図">
      <svg className="sequence-diagram" viewBox={`0 0 ${width} ${height}`} width={width} height={height}>
        <defs>
          <marker id="sequence-arrow-head" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="#526b82" />
          </marker>
        </defs>
        {participants.map((participant) => {
          const x = participantPositions.get(participant.id) ?? marginX;
          const label = text(participant.data?.label, "関係者");
          return (
            <g key={participant.id}>
              <rect x={x - 85} y={headerY} width="170" height={headerHeight} rx="4" className="sequence-participant-box" />
              <text x={x} y={headerY + 27} textAnchor="middle" className="sequence-participant-label">
                {label.slice(0, 22)}
              </text>
              <line x1={x} y1={headerY + headerHeight} x2={x} y2={height - 18} className="sequence-lifeline" />
            </g>
          );
        })}
        {messages.map((message, index) => {
          const sourceX = participantPositions.get(message.source) ?? marginX;
          const targetX = participantPositions.get(message.target) ?? marginX;
          const y = messageStartY + index * messageRowHeight;
          const label = text(message.label, "やり取り");
          const isCandidate = message.style?.strokeDasharray;
          const isSelfMessage = sourceX === targetX;
          const messagePath = isSelfMessage
            ? `M ${sourceX} ${y} H ${sourceX + 42} V ${y + 22} H ${sourceX + 2}`
            : `M ${sourceX} ${y} H ${targetX}`;
          return (
            <g key={message.id}>
              <title>{label}</title>
              <path
                d={messagePath}
                className={`sequence-message-line${isCandidate ? " candidate" : ""}`}
                markerEnd="url(#sequence-arrow-head)"
              />
              <text
                x={isSelfMessage ? sourceX + 22 : (sourceX + targetX) / 2}
                y={y - 9}
                textAnchor="middle"
                className="sequence-message-label"
              >
                {label.slice(0, 38)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function ProcessGraph({ graph, view }: { graph: Graph; view: "flowchart" | "sequence" }) {
  if (view === "sequence") {
    return <SequenceDiagram graph={graph} />;
  }

  return (
    <div className="process-flow-canvas">
      <ReactFlow
        nodes={graph.nodes}
        edges={graph.edges}
        nodeTypes={flowchartNodeTypes}
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
  );
}

function updateDraftEntity(
  processState: ProcessModelState,
  collection: ProcessCollection,
  index: number,
  changes: ProcessEntity,
): ProcessModelState {
  const next = cloneProcessState(processState);
  const items = [...(next[collection] ?? [])];
  items[index] = { ...items[index], ...changes };
  next[collection] = items;
  return next;
}

function ProcessEditor({
  view,
  processState,
  onChange,
}: {
  view: "flowchart" | "sequence";
  processState: ProcessModelState;
  onChange: (collection: ProcessCollection, index: number, changes: ProcessEntity) => void;
}) {
  if (view === "flowchart") {
    return (
      <div className="process-editor-panel" aria-label="フローチャート編集">
        <div className="process-editor-section">
          <strong>処理</strong>
          {(processState.nodes ?? []).map((node, index) => (
            <label className="process-editor-row" key={text(node.nodeId, `node-${index}`)}>
              <span>{text(node.nodeType, "activity") === "decision" ? "分岐" : "処理"}</span>
              <input
                value={text(node.label, "")}
                onChange={(event) => onChange("nodes", index, { label: event.target.value })}
                aria-label={`処理 ${index + 1} の名称`}
              />
            </label>
          ))}
        </div>
        <div className="process-editor-section">
          <strong>つながり・条件</strong>
          {(processState.edges ?? []).map((edge, index) => (
            <label className="process-editor-row" key={text(edge.edgeId, `edge-${index}`)}>
              <span>{`${text(edge.sourceNodeId, "開始")} → ${text(edge.targetNodeId, "終了")}`}</span>
              <input
                value={text(edge.label ?? edge.condition, "")}
                onChange={(event) => onChange("edges", index, { label: event.target.value })}
                aria-label={`つながり ${index + 1} の条件`}
                placeholder="条件（任意）"
              />
            </label>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="process-editor-panel" aria-label="シーケンス図編集">
      <div className="process-editor-section">
        <strong>関係者</strong>
        {(processState.participants ?? []).map((participant, index) => (
          <label className="process-editor-row" key={text(participant.participantId, `participant-${index}`)}>
            <span>関係者 {index + 1}</span>
            <input
              value={text(participant.name, "")}
              onChange={(event) => onChange("participants", index, { name: event.target.value })}
              aria-label={`関係者 ${index + 1} の名称`}
            />
          </label>
        ))}
      </div>
      <div className="process-editor-section">
        <strong>やり取り</strong>
        {[...(processState.interactions ?? [])]
          .sort((left, right) => number(left.sequence, 0) - number(right.sequence, 0))
          .map((interaction, index) => {
            const sourceIndex = (processState.interactions ?? []).findIndex(
              (item) => item.interactionId === interaction.interactionId,
            );
            return (
              <label className="process-editor-row" key={text(interaction.interactionId, `interaction-${index}`)}>
                <span>{`${number(interaction.sequence, index + 1)}. ${text(interaction.action, "やり取り")}`}</span>
                <input
                  value={text(interaction.action, "")}
                  onChange={(event) => onChange("interactions", sourceIndex, { action: event.target.value })}
                  aria-label={`やり取り ${index + 1} の内容`}
                />
              </label>
            );
          })}
      </div>
    </div>
  );
}

function processModelErrorMessage(error: unknown, action: "save" | "command") {
  if (error instanceof ApiError) {
    if (error.status === 409 || error.detail === "process_model_version_conflict") {
      return "別の更新が反映されています。画面を更新してから、もう一度お試しください。";
    }
    if (error.detail === "process_model_not_available") {
      return "このインタビューには、まだ編集できる処理モデルがありません。";
    }
    if (error.detail === "process_model_edit_not_allowed_after_approval") {
      return "承認済みの記録は直接編集できません。新しい記録を作成してください。";
    }
    if (error.detail === "process_model_edit_failed") {
      return "指示を処理できませんでした。内容を短くして、もう一度お試しください。";
    }
    if (error.detail === "interview_state_version_conflict") {
      return "インタビューの状態が更新されています。画面を更新してから、もう一度お試しください。";
    }
    if (error.detail === "requirement_entity_not_found") {
      return "更新対象の要件を特定できませんでした。要件名を含めて、もう一度指示してください。";
    }
    if (error.detail === "requirement_state_not_available") {
      return "この記録には編集できる要件情報がありません。";
    }
    if (error.detail === "requirement_value_required") {
      return "要件の変更内容を指定してください。";
    }
    if (error.detail === "invalid_interview_state_version") {
      return "インタビュー状態を読み込めませんでした。画面を更新してください。";
    }
    if (error.status === 422) {
      return "要件または処理モデルの変更対象を特定できませんでした。要件名や処理名を含めて指示してください。";
    }
    if (error.detail === "network_error") {
      return "接続に失敗しました。通信状態を確認して、もう一度お試しください。";
    }
  }
  return action === "save"
    ? "処理モデルを保存できませんでした。もう一度お試しください。"
    : "指示を反映できませんでした。もう一度お試しください。";
}

export function ProcessModelPanel({
  interviewState,
  canEdit = false,
  onSaveProcessModel,
  onEditProcessModel,
}: ProcessModelPanelProps) {
  const [view, setView] = useState<"flowchart" | "sequence">("flowchart");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [draftProcessState, setDraftProcessState] = useState<ProcessModelState | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [commandInput, setCommandInput] = useState("");
  const [isSendingCommand, setIsSendingCommand] = useState(false);
  const [commandMessages, setCommandMessages] = useState<CommandMessage[]>([]);
  const [notice, setNotice] = useState("");
  const processState = interviewState?.processState;
  const editableProcessState = draftProcessState ?? processState ?? {};
  const flowchart = useMemo(() => buildFlowchart(editableProcessState), [editableProcessState]);
  const sequence = useMemo(() => buildSequence(editableProcessState), [editableProcessState]);
  const isStructuredProfile = interviewState?.interviewProfile === "business_process"
    || interviewState?.interviewProfile === "system_requirement";
  const processStatus = interviewState?.interviewProfile === "business_process"
    ? "present"
    : interviewState?.applicabilityState?.process?.status ?? "unknown";

  useEffect(() => {
    if (!isFullscreen) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSaving) {
        setIsFullscreen(false);
        setIsEditing(false);
        setDraftProcessState(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isFullscreen, isSaving]);

  if (!isStructuredProfile) return null;

  const activeGraph = view === "flowchart" ? flowchart : sequence;
  const graphReady = view === "flowchart"
    ? flowchart.nodes.length >= 2 && flowchart.edges.length >= 1
    : sequence.nodes.length >= 2 && sequence.edges.length >= 1;
  const hasRenderableGraph = (
    flowchart.nodes.length >= 2 && flowchart.edges.length >= 1
  ) || (
    sequence.nodes.length >= 2 && sequence.edges.length >= 1
  );
  const baseProcessVersion = processVersion(processState ?? {}, interviewState);
  const baseStateVersion = number(interviewState?.stateVersion, 0);
  const hasUnsavedChanges = Boolean(
    isEditing
      && draftProcessState
      && JSON.stringify(draftProcessState) !== JSON.stringify(processState ?? {}),
  );

  function openEditor() {
    if (!canEdit || !processState) return;
    setDraftProcessState(cloneProcessState(processState));
    setIsEditing(true);
    setNotice("");
  }

  function closeFullscreen() {
    if (isSaving) return;
    setIsFullscreen(false);
    setIsEditing(false);
    setDraftProcessState(null);
    setNotice("");
  }

  function handleDraftChange(
    collection: ProcessCollection,
    index: number,
    changes: ProcessEntity,
  ) {
    setDraftProcessState((current) => (
      current ? updateDraftEntity(current, collection, index, changes) : current
    ));
  }

  async function handleSave() {
    if (!draftProcessState || isSaving) return;
    setIsSaving(true);
    setNotice("");
    try {
      await onSaveProcessModel(draftProcessState, baseProcessVersion, baseStateVersion);
      setDraftProcessState(null);
      setIsEditing(false);
      setNotice("修正を保存しました。");
    } catch (error) {
      setNotice(processModelErrorMessage(error, "save"));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSendCommand() {
    const instruction = commandInput.trim();
    if (!instruction || isSendingCommand) return;
    if (hasUnsavedChanges) {
      setNotice("手動修正を先に保存してから、指示を送信してください。");
      return;
    }
    setCommandInput("");
    setCommandMessages((current) => [...current, { role: "user" as const, text: instruction }].slice(-4));
    setIsSendingCommand(true);
    setNotice("");
    try {
      const result = await onEditProcessModel(instruction, baseProcessVersion, baseStateVersion);
      setCommandMessages((current) => [...current, { role: "assistant" as const, text: result.reply }].slice(-4));
    } catch (error) {
      setCommandMessages((current) => [
        ...current,
        { role: "error" as const, text: processModelErrorMessage(error, "command") },
      ].slice(-4));
    } finally {
      setIsSendingCommand(false);
    }
  }

  const modelEditor = canEdit && isEditing && draftProcessState ? (
    <ProcessEditor
      view={view}
      processState={draftProcessState}
      onChange={handleDraftChange}
    />
  ) : null;

  const commandBar = canEdit ? (
    <>
      {commandMessages.length > 0 ? (
        <div className="process-command-history" aria-live="polite">
          {commandMessages.slice(-3).map((message, index) => (
            <p className={`process-command-message ${message.role}`} key={`${message.role}-${index}-${message.text}`}>
              <span>{message.role === "user" ? "あなた" : message.role === "assistant" ? "AI" : "通知"}</span>
              {message.text}
            </p>
          ))}
        </div>
      ) : null}
      <div className="process-command-bar">
        <input
          value={commandInput}
          onChange={(event) => setCommandInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void handleSendCommand();
            }
          }}
          disabled={isSendingCommand || hasUnsavedChanges}
          placeholder="例：検索結果に一致度スコアを表示し、高い順に並べる"
          aria-label="要件・処理モデルへの編集指示"
        />
        <button
          type="button"
          className="primary compact"
          onClick={() => void handleSendCommand()}
          disabled={!commandInput.trim() || isSendingCommand || hasUnsavedChanges}
        >
          {isSendingCommand ? "反映中…" : "送信"}
        </button>
      </div>
    </>
  ) : null;

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
          <div className="process-model-header-actions">
            <span className="status-pill muted">会話から整理</span>
            {hasRenderableGraph ? (
              <button type="button" className="ghost compact" onClick={() => setIsFullscreen(true)}>
                全画面
              </button>
            ) : null}
          </div>
        </div>
        <div className="process-model-tabs" role="tablist" aria-label="処理モデル表示切替">
          <button type="button" role="tab" aria-selected={view === "flowchart"} className={view === "flowchart" ? "active" : ""} onClick={() => setView("flowchart")}>フローチャート</button>
          <button type="button" role="tab" aria-selected={view === "sequence"} className={view === "sequence" ? "active" : ""} onClick={() => setView("sequence")}>シーケンス図</button>
        </div>
        {graphReady ? <ProcessGraph graph={activeGraph} view={view} /> : (
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

      {isFullscreen ? (
        <div className="process-model-backdrop">
          <section className="process-model-fullscreen" role="dialog" aria-modal="true" aria-label="処理モデル全画面表示">
            <div className="process-fullscreen-header">
              <div>
                <strong>要件・処理モデル</strong>
                <span>要件、フローチャート、シーケンス図</span>
              </div>
              <div className="process-fullscreen-actions">
                {canEdit && !isEditing ? (
                  <button type="button" className="ghost" onClick={openEditor}>編集</button>
                ) : null}
                {canEdit && isEditing ? (
                  <button type="button" className="primary" onClick={() => void handleSave()} disabled={isSaving}>
                    {isSaving ? "保存中…" : "保存"}
                  </button>
                ) : null}
                <button type="button" className="ghost" onClick={closeFullscreen} disabled={isSaving}>閉じる</button>
              </div>
            </div>
            <div className="process-model-tabs" role="tablist" aria-label="全画面の処理モデル表示切替">
              <button type="button" role="tab" aria-selected={view === "flowchart"} className={view === "flowchart" ? "active" : ""} onClick={() => setView("flowchart")}>フローチャート</button>
              <button type="button" role="tab" aria-selected={view === "sequence"} className={view === "sequence" ? "active" : ""} onClick={() => setView("sequence")}>シーケンス図</button>
            </div>
            <div className={`process-fullscreen-content ${isEditing ? "editing" : ""}`}>
              <div className="process-fullscreen-canvas">
                {graphReady ? <ProcessGraph graph={activeGraph} view={view} /> : (
                  <p className="empty process-model-empty">
                    {view === "flowchart"
                      ? "フローチャートの表示に必要な処理とつながりを整理しています。"
                      : "シーケンス図の表示に必要な関係者とやり取りを整理しています。"}
                  </p>
                )}
              </div>
              {modelEditor}
            </div>
            {notice ? <p className="process-model-notice" role="status">{notice}</p> : null}
            {commandBar}
          </section>
        </div>
      ) : null}
    </section>
  );
}
