import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
  Position,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ApiError } from "../../../lib/api";
import { useI18n, type Translate } from "../../../i18n";
import { formatNumber } from "../../../lib/date";
import type { InterviewState, ProcessModelState } from "../../../types/app";
import {
  fallbackFlowchartLayout,
  flowchartNodeDimensions,
  flowchartShape,
  layoutFlowchart,
  resolveFlowchartNodeType,
  type FlowchartGraph,
  type FlowchartNodeKind,
} from "../flowchartLayout";

type ProcessCollection = "participants" | "nodes" | "edges" | "interactions";
type ProcessEntity = Record<string, unknown>;
type Graph = { nodes: Node[]; edges: Edge[] };
type CommandMessage = { role: "user" | "assistant" | "error"; text: string };
type FlowchartNodeData = { label?: string; nodeType?: FlowchartNodeKind; candidate?: boolean };
type FlowchartNodeType = Node<FlowchartNodeData>;
type SmoothstepFlowEdge = Edge & {
  pathOptions?: { borderRadius?: number; offset?: number };
};

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

function FlowchartNode({ data }: NodeProps<FlowchartNodeType>) {
  const nodeType = data.nodeType ?? "activity";
  const shape = flowchartShape(nodeType);
  const label = text(data.label, "");
  return (
    <div
      className={`flowchart-node flowchart-node-${shape}${data.candidate ? " is-candidate" : ""}`}
      data-flowchart-shape={shape}
      aria-label={label}
    >
      <Handle type="target" position={Position.Top} />
      <span title={label}>{label}</span>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const flowchartNodeTypes: NodeTypes = { flowchart: FlowchartNode };

function buildFlowchart(processState: ProcessModelState, t: Translate): Graph {
  const nodes = (processState.nodes ?? [])
    .filter((node) => node.lifecycle !== "superseded")
    .map((node, index): Node => {
      const isCandidate = node.confirmationStatus !== "confirmed";
      const label = text(node.label, t("interview.process.unnamedProcess"));
      const nodeType = resolveFlowchartNodeType(node.nodeType, label);
      const dimensions = flowchartNodeDimensions(label, nodeType);
      return {
        id: text(node.nodeId, `node-${index}`),
        type: "flowchart",
        position: { x: 0, y: 0 },
        data: { label, nodeType, candidate: isCandidate },
        sourcePosition: Position.Bottom,
        targetPosition: Position.Top,
        style: {
          width: dimensions.width,
          height: dimensions.height,
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
    .map((edge, index): SmoothstepFlowEdge => ({
      id: text(edge.edgeId, `edge-${index}`),
      source: text(edge.sourceNodeId, ""),
      target: text(edge.targetNodeId, ""),
      label: text(edge.label ?? edge.condition, "") || undefined,
      type: "smoothstep",
      pathOptions: { borderRadius: 12, offset: 28 },
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: edge.confirmationStatus === "confirmed" ? undefined : { strokeDasharray: "5 4" },
    }));
  return { nodes, edges };
}

function buildSequence(processState: ProcessModelState, t: Translate): Graph {
  const participants = (processState.participants ?? []).filter(
    (participant) => participant.lifecycle !== "superseded",
  );
  const nodes = participants.map((participant, index): Node => {
    const isCandidate = participant.confirmationStatus !== "confirmed";
    return {
      id: text(participant.participantId, `participant-${index}`),
      position: { x: index * 210, y: 20 },
      data: { label: text(participant.name, t("interview.process.participant")) },
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
      label: text(interaction.action, t("interview.process.interaction")),
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
      style: interaction.confirmationStatus === "confirmed" ? undefined : { strokeDasharray: "5 4" },
    }));
  return { nodes, edges };
}

function SequenceDiagram({ graph, t }: { graph: Graph; t: Translate }) {
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
    <div className="sequence-diagram-scroll" role="img" aria-label={t("interview.process.umlAria")}>
      <svg className="sequence-diagram" viewBox={`0 0 ${width} ${height}`} width={width} height={height}>
        <defs>
          <marker id="sequence-arrow-head" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="#526b82" />
          </marker>
        </defs>
        {participants.map((participant) => {
          const x = participantPositions.get(participant.id) ?? marginX;
          const label = text(participant.data?.label, t("interview.process.participant"));
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
          const label = text(message.label, t("interview.process.interaction"));
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

function FlowchartCanvas({ graph }: { graph: FlowchartGraph }) {
  const reactFlow = useReactFlow();
  const [layoutedGraph, setLayoutedGraph] = useState<FlowchartGraph>(() => fallbackFlowchartLayout(graph));

  useEffect(() => {
    let isCurrent = true;
    const fallback = fallbackFlowchartLayout(graph);
    setLayoutedGraph(fallback);
    void layoutFlowchart(graph)
      .then((next) => {
        if (isCurrent) setLayoutedGraph(next);
      })
      .catch(() => {
        if (isCurrent) setLayoutedGraph(fallback);
      });
    return () => {
      isCurrent = false;
    };
  }, [graph]);

  useEffect(() => {
    if (layoutedGraph.nodes.length === 0) return undefined;
    const frame = window.requestAnimationFrame(() => {
      reactFlow.fitView({ padding: 0.2, duration: 180 });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [layoutedGraph, reactFlow]);

  return (
    <ReactFlow
      nodes={layoutedGraph.nodes}
      edges={layoutedGraph.edges}
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
  );
}

function ProcessGraph({ graph, view, t }: { graph: Graph; view: "flowchart" | "sequence"; t: Translate }) {
  if (view === "sequence") {
    return <SequenceDiagram graph={graph} t={t} />;
  }

  return (
    <div className="process-flow-canvas">
      <ReactFlowProvider>
        <FlowchartCanvas graph={graph} />
      </ReactFlowProvider>
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
  t,
}: {
  view: "flowchart" | "sequence";
  processState: ProcessModelState;
  onChange: (collection: ProcessCollection, index: number, changes: ProcessEntity) => void;
  t: Translate;
}) {
  if (view === "flowchart") {
    return (
      <div className="process-editor-panel" aria-label={t("interview.process.editorFlowchartAria")}>
        <div className="process-editor-section">
          <strong>{t("interview.process.unnamedProcess")}</strong>
          {(processState.nodes ?? []).map((node, index) => (
            <label className="process-editor-row" key={text(node.nodeId, `node-${index}`)}>
              <span>{text(node.nodeType, "activity") === "decision" ? t("interview.process.decision") : t("interview.process.unnamedProcess")}</span>
              <input
                value={text(node.label, "")}
                onChange={(event) => onChange("nodes", index, { label: event.target.value })}
                aria-label={t("interview.process.nodeNameAria", { index: index + 1 })}
              />
            </label>
          ))}
        </div>
        <div className="process-editor-section">
          <strong>{t("interview.process.connection")}</strong>
          {(processState.edges ?? []).map((edge, index) => (
            <label className="process-editor-row" key={text(edge.edgeId, `edge-${index}`)}>
              <span>{`${text(edge.sourceNodeId, t("interview.process.start"))} → ${text(edge.targetNodeId, t("interview.process.end"))}`}</span>
              <input
                value={text(edge.label ?? edge.condition, "")}
                onChange={(event) => onChange("edges", index, { label: event.target.value })}
                aria-label={t("interview.process.edgeConditionAria", { index: index + 1 })}
                placeholder={t("interview.process.optionalCondition")}
              />
            </label>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="process-editor-panel" aria-label={t("interview.process.editorSequenceAria")}>
      <div className="process-editor-section">
        <strong>{t("interview.process.participantSection")}</strong>
        {(processState.participants ?? []).map((participant, index) => (
          <label className="process-editor-row" key={text(participant.participantId, `participant-${index}`)}>
            <span>{t("interview.process.participantName", { index: index + 1 })}</span>
            <input
              value={text(participant.name, "")}
              onChange={(event) => onChange("participants", index, { name: event.target.value })}
              aria-label={t("interview.process.participantNameAria", { index: index + 1 })}
            />
          </label>
        ))}
      </div>
      <div className="process-editor-section">
        <strong>{t("interview.process.interactionSection")}</strong>
        {[...(processState.interactions ?? [])]
          .sort((left, right) => number(left.sequence, 0) - number(right.sequence, 0))
          .map((interaction, index) => {
            const sourceIndex = (processState.interactions ?? []).findIndex(
              (item) => item.interactionId === interaction.interactionId,
            );
            return (
              <label className="process-editor-row" key={text(interaction.interactionId, `interaction-${index}`)}>
                <span>{t("interview.process.numberedInteraction", { index: number(interaction.sequence, index + 1), action: text(interaction.action, t("interview.process.interaction")) })}</span>
                <input
                  value={text(interaction.action, "")}
                  onChange={(event) => onChange("interactions", sourceIndex, { action: event.target.value })}
                  aria-label={t("interview.process.interactionContentAria", { index: index + 1 })}
                />
              </label>
            );
          })}
      </div>
    </div>
  );
}

function processModelErrorMessage(error: unknown, action: "save" | "command", t: Translate) {
  if (error instanceof ApiError) {
    if (error.status === 409 || error.detail === "process_model_version_conflict") {
      return t("errors.processVersionConflict");
    }
    if (error.detail === "process_model_not_available") {
      return t("errors.processUnavailable");
    }
    if (error.detail === "process_model_edit_not_allowed_after_approval") {
      return t("errors.processEditAfterApproval");
    }
    if (error.detail === "process_model_edit_failed") {
      return t("errors.processCommandFailed");
    }
    if (error.detail === "interview_state_version_conflict") {
      return t("errors.interviewVersionConflict");
    }
    if (error.detail === "requirement_entity_not_found") {
      return t("errors.processTargetNotFound");
    }
    if (error.detail === "requirement_state_not_available") {
      return t("errors.requirementUnavailable");
    }
    if (error.detail === "requirement_value_required") {
      return t("errors.requirementValueRequired");
    }
    if (error.detail === "invalid_interview_state_version") {
      return t("errors.invalidInterviewState");
    }
    if (error.status === 422) {
      return t("errors.processTargetNotFound");
    }
    if (error.detail === "network_error") {
      return t("errors.connectionFailed");
    }
  }
  return action === "save"
    ? t("errors.processSaveFailed")
    : t("errors.processCommandFailed");
}

export function ProcessModelPanel({
  interviewState,
  canEdit = false,
  onSaveProcessModel,
  onEditProcessModel,
}: ProcessModelPanelProps) {
  const { t, locale } = useI18n();
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
  const flowchart = useMemo(() => buildFlowchart(editableProcessState, t), [editableProcessState, t]);
  const sequence = useMemo(() => buildSequence(editableProcessState, t), [editableProcessState, t]);
  const isStructuredProfile = interviewState?.interviewProfile === "business_process"
    || interviewState?.interviewProfile === "system_requirement";
  const processStatus = interviewState?.interviewProfile === "business_process"
    ? "present"
    : interviewState?.applicabilityState?.process?.status ?? "unknown";

  useEffect(() => {
    if (!isFullscreen) return undefined;
    const previousOverflow = document.body.style.overflow;
    const previousBodyOverscrollBehavior = document.body.style.overscrollBehavior;
    const previousDocumentOverflow = document.documentElement.style.overflow;
    const root = document.getElementById("root");
    const previousRootInert = root?.inert ?? false;
    const previousRootAriaHidden = root ? root.getAttribute("aria-hidden") : null;
    document.body.style.overflow = "hidden";
    document.body.style.overscrollBehavior = "none";
    document.documentElement.style.overflow = "hidden";
    if (root) {
      root.inert = true;
      root.setAttribute("aria-hidden", "true");
    }
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
      document.body.style.overscrollBehavior = previousBodyOverscrollBehavior;
      document.documentElement.style.overflow = previousDocumentOverflow;
      if (root) {
        root.inert = previousRootInert;
        if (previousRootAriaHidden === null) {
          root.removeAttribute("aria-hidden");
        } else {
          root.setAttribute("aria-hidden", previousRootAriaHidden);
        }
      }
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
      setNotice(t("interview.process.saved"));
    } catch (error) {
      setNotice(processModelErrorMessage(error, "save", t));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSendCommand() {
    const instruction = commandInput.trim();
    if (!instruction || isSendingCommand) return;
    if (hasUnsavedChanges) {
      setNotice(t("interview.process.manualSaveFirst"));
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
        { role: "error" as const, text: processModelErrorMessage(error, "command", t) },
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
      t={t}
    />
  ) : null;

  const commandBar = canEdit ? (
    <>
      {commandMessages.length > 0 ? (
        <div className="process-command-history" aria-live="polite">
          {commandMessages.slice(-3).map((message, index) => (
            <p className={`process-command-message ${message.role}`} key={`${message.role}-${index}-${message.text}`}>
              {message.role !== "user" ? (
                <span>{message.role === "assistant" ? t("common.ai") : t("common.notification")}</span>
              ) : null}
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
          placeholder={t("interview.process.commandPlaceholder")}
          aria-label={t("interview.process.commandAria")}
        />
        <button
          type="button"
          className="primary compact"
          onClick={() => void handleSendCommand()}
          disabled={!commandInput.trim() || isSendingCommand || hasUnsavedChanges}
        >
          {isSendingCommand ? t("interview.process.applying") : t("common.send")}
        </button>
      </div>
    </>
  ) : null;

  return (
    <section className="process-model-panel" aria-label={t("interview.process.ariaLabel")}>
      {processStatus === "unknown" ? (
        <p className="empty process-model-empty">
          {t("interview.process.unknown")}
        </p>
      ) : processStatus === "not_applicable" ? (
        <p className="empty process-model-empty">
          {t("interview.process.notApplicable")}
        </p>
      ) : <>
        <div className="process-model-header">
          <div>
            <strong>{t("interview.process.title")}</strong>
            <p>{t("interview.process.description")}</p>
          </div>
          <div className="process-model-header-actions">
            <span className="status-pill muted">{t("interview.process.fromConversation")}</span>
            {hasRenderableGraph ? (
              <button type="button" className="ghost compact" onClick={() => setIsFullscreen(true)}>
                {t("interview.process.fullscreen")}
              </button>
            ) : null}
          </div>
        </div>
        <div className="process-model-tabs" role="tablist" aria-label={t("interview.process.tabAria")}>
          <button type="button" role="tab" aria-selected={view === "flowchart"} className={view === "flowchart" ? "active" : ""} onClick={() => setView("flowchart")}>{t("interview.process.flowchart")}</button>
          <button type="button" role="tab" aria-selected={view === "sequence"} className={view === "sequence" ? "active" : ""} onClick={() => setView("sequence")}>{t("interview.process.sequence")}</button>
        </div>
        {graphReady ? <ProcessGraph graph={activeGraph} view={view} t={t} /> : (
          <p className="empty process-model-empty">
            {view === "flowchart"
              ? t("interview.process.flowchartEmpty")
              : t("interview.process.sequenceEmpty")}
          </p>
        )}
        {view === "sequence" && sequence.edges.length > 0 ? (
          <ol className="sequence-interaction-list">
            {[...(processState?.interactions ?? [])]
              .filter((interaction) => interaction.lifecycle !== "superseded")
              .sort((left, right) => number(left.sequence, 0) - number(right.sequence, 0))
              .map((interaction, index) => (
                <li key={text(interaction.interactionId, `interaction-${index}`)}>
                  <span>{formatNumber(number(interaction.sequence, index + 1), locale)}</span>
                  {interaction.data ? t("interview.process.dataWithValue", { action: text(interaction.action, t("interview.process.interaction")), data: text(interaction.data, "") }) : text(interaction.action, t("interview.process.interaction"))}
                </li>
              ))}
          </ol>
        ) : null}
      </>}

      {isFullscreen ? createPortal(
        <div className="process-model-backdrop">
          <section className="process-model-fullscreen" role="dialog" aria-modal="true" aria-label={t("interview.process.fullscreenAria")}>
            <div className="process-fullscreen-header">
              <div>
                <strong>{t("interview.process.fullscreenTitle")}</strong>
                <span>{t("interview.process.fullscreenDescription")}</span>
              </div>
              <div className="process-fullscreen-actions">
                {canEdit && !isEditing ? (
                  <button type="button" className="ghost" onClick={openEditor}>{t("interview.process.edit")}</button>
                ) : null}
                {canEdit && isEditing ? (
                  <button type="button" className="primary" onClick={() => void handleSave()} disabled={isSaving}>
                    {isSaving ? t("common.saving") : t("interview.process.save")}
                  </button>
                ) : null}
                <button type="button" className="ghost" onClick={closeFullscreen} disabled={isSaving} autoFocus>{t("interview.process.close")}</button>
              </div>
            </div>
            <div className="process-model-tabs" role="tablist" aria-label={t("interview.process.fullscreenTabAria")}>
              <button type="button" role="tab" aria-selected={view === "flowchart"} className={view === "flowchart" ? "active" : ""} onClick={() => setView("flowchart")}>{t("interview.process.flowchart")}</button>
              <button type="button" role="tab" aria-selected={view === "sequence"} className={view === "sequence" ? "active" : ""} onClick={() => setView("sequence")}>{t("interview.process.sequence")}</button>
            </div>
            <div className={`process-fullscreen-content ${isEditing ? "editing" : ""}`}>
              <div className="process-fullscreen-canvas">
                {graphReady ? <ProcessGraph graph={activeGraph} view={view} t={t} /> : (
                  <p className="empty process-model-empty">
                    {view === "flowchart"
                      ? t("interview.process.fullscreenFlowchartEmpty")
                      : t("interview.process.fullscreenSequenceEmpty")}
                  </p>
                )}
              </div>
              {modelEditor}
            </div>
            {notice ? <p className="process-model-notice" role="status">{notice}</p> : null}
            {commandBar}
          </section>
        </div>,
        document.body,
      ) : null}
    </section>
  );
}
