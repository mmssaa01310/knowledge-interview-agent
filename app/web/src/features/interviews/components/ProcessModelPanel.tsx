import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Background,
  ControlButton,
  Controls,
  Handle,
  MarkerType,
  Panel,
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
type FlowchartNodeData = { label?: string; nodeType?: FlowchartNodeKind; candidate?: boolean };
type FlowchartNodeType = Node<FlowchartNodeData>;
type SequenceInteractionType = "message" | "return" | "async" | "notification" | "handoff" | "exception";
type SequenceFragmentType = "none" | "alt" | "opt" | "loop";
type SequenceParticipantData = { label?: string; kind?: string; candidate?: boolean };
type SequenceMessageData = {
  sequence?: number;
  data?: string;
  interactionType?: SequenceInteractionType;
  fragmentType?: SequenceFragmentType;
  fragmentId?: string;
  fragmentLabel?: string;
  candidate?: boolean;
};
type SequenceGraph = {
  nodes: Array<Node<SequenceParticipantData>>;
  edges: Array<Edge<SequenceMessageData>>;
};
type SmoothstepFlowEdge = Edge & {
  pathOptions?: { borderRadius?: number; offset?: number };
};

type ProcessFlowInfoProps = {
  t: Translate;
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

export function ProcessFlowInfo({ t }: ProcessFlowInfoProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [tooltipPosition, setTooltipPosition] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const tooltipId = "process-flow-info-tooltip";

  useLayoutEffect(() => {
    if (!isOpen) return undefined;

    const updateTooltipPosition = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      const rect = trigger.getBoundingClientRect();
      const tooltipWidth = Math.min(360, Math.max(0, window.innerWidth - 32));
      const left = Math.max(
        16,
        Math.min(rect.right - tooltipWidth, window.innerWidth - tooltipWidth - 16),
      );
      setTooltipPosition({ top: rect.bottom + 8, left });
    };

    updateTooltipPosition();
    window.addEventListener("resize", updateTooltipPosition);
    window.addEventListener("scroll", updateTooltipPosition, true);
    return () => {
      window.removeEventListener("resize", updateTooltipPosition);
      window.removeEventListener("scroll", updateTooltipPosition, true);
    };
  }, [isOpen]);

  const openTooltip = () => {
    setTooltipPosition(null);
    setIsOpen(true);
  };

  const closeTooltip = () => setIsOpen(false);

  return (
    <>
      <span
        ref={triggerRef}
        className="process-flow-info"
        role="img"
        tabIndex={0}
        aria-describedby={isOpen ? tooltipId : undefined}
        aria-expanded={isOpen}
        onMouseEnter={openTooltip}
        onMouseLeave={closeTooltip}
        onFocus={openTooltip}
        onBlur={closeTooltip}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            closeTooltip();
          }
        }}
        aria-label={t("interview.process.infoLabel")}
      >
        <span aria-hidden="true">i</span>
      </span>
      {isOpen && tooltipPosition && typeof document !== "undefined" ? createPortal(
        <div
          id={tooltipId}
          className="process-flow-info-tooltip"
          role="tooltip"
          style={{ top: tooltipPosition.top, left: tooltipPosition.left }}
        >
          <ul>
            <li>{t("interview.process.infoFlowchart")}</li>
            <li>{t("interview.process.infoSequence")}</li>
            <li>{t("interview.process.infoTiming")}</li>
          </ul>
        </div>,
        document.body,
      ) : null}
    </>
  );
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

const SEQUENCE_INTERACTION_TYPES: SequenceInteractionType[] = [
  "message",
  "return",
  "async",
  "notification",
  "handoff",
  "exception",
];
const SEQUENCE_FRAGMENT_TYPES: SequenceFragmentType[] = ["none", "alt", "opt", "loop"];

function sequenceInteractionType(value: unknown, label: string): SequenceInteractionType {
  if (SEQUENCE_INTERACTION_TYPES.includes(value as SequenceInteractionType)) {
    return value as SequenceInteractionType;
  }
  if (/(?:例外|異常|エラー|失敗|権限なし|exception|error|failure)/iu.test(label)) return "exception";
  if (/(?:非同期|バックグラウンド|キュー|async|background|queue)/iu.test(label)) return "async";
  if (/(?:通知|notify|notification)/iu.test(label)) return "notification";
  if (/(?:引き継|エスカレーション|handoff)/iu.test(label)) return "handoff";
  if (/(?:応答|返却|結果を返|return|response)/iu.test(label)) return "return";
  return "message";
}

function sequenceFragmentType(value: unknown, label: string): SequenceFragmentType {
  if (SEQUENCE_FRAGMENT_TYPES.includes(value as SequenceFragmentType)) {
    return value as SequenceFragmentType;
  }
  if (/(?:繰り返|反復|毎回|各件|loop|retry)/iu.test(label)) return "loop";
  if (/(?:任意|必要に応じ|場合のみ|opt)/iu.test(label)) return "opt";
  if (/(?:条件|分岐|権限|件数|場合|正常|異常|alt)/iu.test(label)) return "alt";
  return "none";
}

function buildSequence(processState: ProcessModelState, t: Translate): SequenceGraph {
  const participants = (processState.participants ?? []).filter(
    (participant) => participant.lifecycle !== "superseded",
  );
  const nodes = participants.map((participant, index): Node<SequenceParticipantData> => {
    const isCandidate = participant.confirmationStatus !== "confirmed";
    return {
      id: text(participant.participantId, `participant-${index}`),
      position: { x: index * 210, y: 20 },
      data: {
        label: text(participant.name, t("interview.process.participant")),
        kind: text(participant.kind, "unknown"),
        candidate: isCandidate,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
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
    .map((interaction, index): Edge<SequenceMessageData> => {
      const label = text(interaction.action, t("interview.process.interaction"));
      return {
        id: text(interaction.interactionId, `interaction-${index}`),
        source: text(interaction.sourceParticipantId, ""),
        target: text(interaction.targetParticipantId, ""),
        label,
        data: {
          sequence: Math.max(1, number(interaction.sequence, index + 1)),
          data: text(interaction.data, "") || undefined,
          interactionType: sequenceInteractionType(interaction.interactionType, label),
          fragmentType: sequenceFragmentType(interaction.fragmentType, label),
          fragmentId: text(interaction.fragmentId, "") || undefined,
          fragmentLabel: text(interaction.fragmentLabel, "") || undefined,
          candidate: interaction.confirmationStatus !== "confirmed",
        },
        style: interaction.confirmationStatus === "confirmed" ? undefined : { strokeDasharray: "5 4" },
      };
    });
  return { nodes, edges };
}

function visualTextLength(value: string) {
  return Array.from(value).reduce(
    (length, character) => length + (character.charCodeAt(0) > 0xff ? 1.8 : 1),
    0,
  );
}

function wrapSequenceText(value: string, maxVisualWidth: number) {
  const lines: string[] = [];
  let current = "";
  let currentWidth = 0;
  for (const character of Array.from(value || "")) {
    if (character === "\n") {
      lines.push(current);
      current = "";
      currentWidth = 0;
      continue;
    }
    const characterWidth = character.charCodeAt(0) > 0xff ? 1.8 : 1;
    if (current && currentWidth + characterWidth > maxVisualWidth) {
      lines.push(current);
      current = character;
      currentWidth = characterWidth;
    } else {
      current += character;
      currentWidth += characterWidth;
    }
  }
  if (current || lines.length === 0) lines.push(current);
  return lines;
}

function clampSequenceScale(value: number) {
  return Math.min(2.5, Math.max(0.45, value));
}

function sequenceTextWithData(message: Edge<SequenceMessageData>, t: Translate) {
  const label = text(message.label, t("interview.process.interaction"));
  const data = text(message.data?.data, "");
  return data ? t("interview.process.dataWithValue", { action: label, data }) : label;
}

type SequenceMessageLayout = {
  message: Edge<SequenceMessageData>;
  top: number;
  center: number;
  height: number;
  lines: string[];
};

type SequenceFragmentLayout = {
  type: Exclude<SequenceFragmentType, "none">;
  top: number;
  bottom: number;
  label: string;
};

function sequenceFragmentLabel(type: Exclude<SequenceFragmentType, "none">) {
  return type.toUpperCase();
}

function SequenceDiagramControlIcon({ type }: { type: "zoomIn" | "zoomOut" | "fitView" }) {
  if (type === "zoomIn") {
    return (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
        <path d="M32 18.133H18.133V32h-4.266V18.133H0v-4.266h13.867V0h4.266v13.867H32z" />
      </svg>
    );
  }
  if (type === "zoomOut") {
    return (
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 5" aria-hidden="true" focusable="false">
        <path d="M0 0h32v4.2H0z" />
      </svg>
    );
  }
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 30" aria-hidden="true" focusable="false">
      <path d="M3.692 4.63c0-.53.4-.938.939-.938h5.215V0H4.708C2.13 0 0 2.054 0 4.63v5.216h3.692V4.631zM27.354 0h-5.2v3.692h5.17c.53 0 .984.4.984.939v5.215H32V4.631A4.624 4.624 0 0027.354 0zm.954 24.83c0 .532-.4.94-.939.94h-5.215v3.768h5.215c2.577 0 4.631-2.13 4.631-4.707v-5.139h-3.692v5.139zm-23.677.94c-.531 0-.939-.4-.939-.94v-5.138H0v5.139c0 2.577 2.13 4.707 4.708 4.707h5.138V25.77H4.631z" />
    </svg>
  );
}

function SequenceDiagram({ graph, t, locale }: { graph: SequenceGraph; t: Translate; locale: Parameters<typeof formatNumber>[1] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const diagramId = useId().replace(/:/gu, "");
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
  const [viewport, setViewport] = useState({ scale: 1, x: 16, y: 16 });
  const participants = graph.nodes;
  const messages = graph.edges;
  const participantLayouts = useMemo(() => {
    const widths = participants.map((participant) => {
      const label = text(participant.data?.label, t("interview.process.participant"));
      return {
        id: participant.id,
        label,
        lines: wrapSequenceText(label, 18),
        width: Math.min(240, Math.max(156, 42 + visualTextLength(label) * 8)),
        candidate: Boolean(participant.data?.candidate),
      };
    });
    const gap = 84;
    const contentWidth = widths.reduce((sum, item) => sum + item.width, 0) + Math.max(widths.length - 1, 0) * gap;
    const width = Math.max(760, contentWidth + 96);
    let cursor = Math.max(48, (width - contentWidth) / 2);
    return {
      width,
      items: widths.map((item) => {
        const next = { ...item, x: cursor + item.width / 2 };
        cursor += item.width + gap;
        return next;
      }),
    };
  }, [participants, t]);
  const participantPositions = useMemo(
    () => new Map(participantLayouts.items.map((participant) => [participant.id, participant])),
    [participantLayouts.items],
  );
  const headerY = 28;
  const headerHeight = Math.max(
    58,
    ...participantLayouts.items.map((participant) => 22 + participant.lines.length * 17),
  );
  const messageStartY = headerY + headerHeight + 58;
  const messageLayouts = useMemo<SequenceMessageLayout[]>(() => {
    let cursor = messageStartY;
    return messages.map((message) => {
      const source = participantPositions.get(message.source);
      const target = participantPositions.get(message.target);
      const availableWidth = Math.max(
        18,
        Math.abs((source?.x ?? 0) - (target?.x ?? 0)) - 34,
      );
      const maxVisualWidth = Math.max(16, Math.min(34, availableWidth / 8));
      const lines = wrapSequenceText(sequenceTextWithData(message, t), maxVisualWidth);
      const height = Math.max(82, 42 + lines.length * 17);
      const layout = { message, top: cursor, center: cursor + height / 2, height, lines };
      cursor += height;
      return layout;
    });
  }, [messageStartY, messages, participantPositions, t]);
  const fragmentLayouts = useMemo<SequenceFragmentLayout[]>(() => {
    const fragments: SequenceFragmentLayout[] = [];
    let current: SequenceFragmentLayout | null = null;
    for (const row of messageLayouts) {
      const type = row.message.data?.fragmentType ?? "none";
      if (type === "none") {
        if (current) fragments.push(current);
        current = null;
        continue;
      }
      const label = text(row.message.data?.fragmentLabel, "");
      const fragmentId = text(row.message.data?.fragmentId, "");
      const currentId: string = current?.label.startsWith(`${fragmentId}|`) ? fragmentId : "";
      if (!current || current.type !== type || (fragmentId && currentId !== fragmentId)) {
        if (current) fragments.push(current);
        current = {
          type,
          top: row.top - 18,
          bottom: row.top + row.height + 16,
          label: `${fragmentId}|${label || sequenceFragmentLabel(type)}`,
        };
      } else {
        current.bottom = row.top + row.height + 16;
      }
    }
    if (current) fragments.push(current);
    return fragments;
  }, [messageLayouts]);
  const height = Math.max(
    360,
    (messageLayouts[messageLayouts.length - 1]?.top ?? messageStartY)
      + (messageLayouts[messageLayouts.length - 1]?.height ?? 0)
      + 48,
  );

  const fitViewport = useCallback(() => {
    const container = containerRef.current;
    if (!container || container.clientWidth <= 0 || container.clientHeight <= 0) return;
    const scale = clampSequenceScale(Math.min(
      (container.clientWidth - 28) / participantLayouts.width,
      (container.clientHeight - 28) / height,
      1,
    ));
    setViewport({
      scale,
      x: Math.max(14, (container.clientWidth - participantLayouts.width * scale) / 2),
      y: Math.max(14, (container.clientHeight - height * scale) / 2),
    });
  }, [height, participantLayouts.width]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const updateSize = () => setContainerSize({
      width: container.clientWidth,
      height: container.clientHeight,
    });
    updateSize();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(updateSize) : null;
    observer?.observe(container);
    window.addEventListener("resize", updateSize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateSize);
    };
  }, []);

  useEffect(() => {
    if (containerSize.width > 0 && containerSize.height > 0) fitViewport();
  }, [containerSize, fitViewport]);

  function zoomAt(nextScale: number, clientX: number, clientY: number) {
    setViewport((current) => {
      const scale = clampSequenceScale(nextScale);
      const diagramX = (clientX - current.x) / current.scale;
      const diagramY = (clientY - current.y) / current.scale;
      return {
        scale,
        x: clientX - diagramX * scale,
        y: clientY - diagramY * scale,
      };
    });
  }

  function handleWheel(event: React.WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    zoomAt(viewport.scale * (event.deltaY < 0 ? 1.12 : 0.89), event.clientX - rect.left, event.clientY - rect.top);
  }

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || (event.target instanceof Element && event.target.closest("button"))) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - drag.x;
    const deltaY = event.clientY - drag.y;
    dragRef.current = { pointerId: drag.pointerId, x: event.clientX, y: event.clientY };
    setViewport((current) => ({ ...current, x: current.x + deltaX, y: current.y + deltaY }));
  }

  function handlePointerUp(event: React.PointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  }

  function zoomBy(factor: number) {
    const container = containerRef.current;
    if (!container) return;
    zoomAt(
      viewport.scale * factor,
      container.clientWidth / 2,
      container.clientHeight / 2,
    );
  }

  const markerId = (name: string) => `${diagramId}-sequence-${name}`;

  return (
    <div
      ref={containerRef}
      className="sequence-diagram-scroll sequence-diagram-viewport"
      role="img"
      aria-label={t("interview.process.umlAria")}
      onWheel={handleWheel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      <svg
        className="sequence-diagram"
        viewBox={`0 0 ${participantLayouts.width} ${height}`}
        width={participantLayouts.width}
        height={height}
        style={{
          transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`,
        }}
        aria-hidden="true"
      >
        <defs>
          <marker id={markerId("arrow")} markerWidth="9" markerHeight="8" refX="8" refY="4" orient="auto">
            <path d="M0,0 L9,4 L0,8" className="sequence-arrow-head" />
          </marker>
          <marker id={markerId("return")} markerWidth="9" markerHeight="8" refX="8" refY="4" orient="auto">
            <path d="M0,0 L9,4 L0,8" className="sequence-return-head" />
          </marker>
        </defs>
        {fragmentLayouts.map((fragment, index) => {
          const labelParts = fragment.label.split("|");
          const label = labelParts[1] || sequenceFragmentLabel(fragment.type);
          const fragmentLabelText = `${sequenceFragmentLabel(fragment.type)}${label && label !== sequenceFragmentLabel(fragment.type) ? ` [${label}]` : ""}`;
          const fragmentLabelLines = wrapSequenceText(fragmentLabelText, 28);
          const fragmentLabelWidth = Math.min(
            participantLayouts.width - 44,
            Math.max(92, Math.max(...fragmentLabelLines.map(visualTextLength)) * 8 + 24),
          );
          return (
            <g key={`${fragment.type}-${fragment.top}-${index}`}>
              <rect
                x="22"
                y={fragment.top}
                width={participantLayouts.width - 44}
                height={fragment.bottom - fragment.top}
                rx="7"
                className={`sequence-fragment sequence-fragment-${fragment.type}`}
              />
              <rect x="22" y={fragment.top} width={fragmentLabelWidth} height={25 + Math.max(0, fragmentLabelLines.length - 1) * 15} className="sequence-fragment-label-box" />
              <text x="34" y={fragment.top + 17} className="sequence-fragment-label">
                {fragmentLabelLines.map((line, lineIndex) => (
                  <tspan key={`${fragment.type}-label-${lineIndex}`} x="34" dy={lineIndex === 0 ? 0 : 15}>{line}</tspan>
                ))}
              </text>
            </g>
          );
        })}
        {participantLayouts.items.map((participant) => (
          <g key={participant.id}>
            <line
              x1={participant.x}
              y1={headerY + headerHeight}
              x2={participant.x}
              y2={height - 18}
              className="sequence-lifeline"
            />
          </g>
        ))}
        {participantLayouts.items.map((participant) => (
          <g key={`${participant.id}-header`}>
            <rect
              x={participant.x - participant.width / 2}
              y={headerY}
              width={participant.width}
              height={Math.max(58, 22 + participant.lines.length * 17)}
              rx="6"
              className={`sequence-participant-box${participant.candidate ? " candidate" : ""}`}
            />
            <text x={participant.x} y={headerY + 21} textAnchor="middle" className="sequence-participant-label">
              {participant.lines.map((line, index) => (
                <tspan key={`${participant.id}-line-${index}`} x={participant.x} dy={index === 0 ? 0 : 17}>{line}</tspan>
              ))}
            </text>
          </g>
        ))}
        {messageLayouts.map((row) => {
          const message = row.message;
          const source = participantPositions.get(message.source);
          const target = participantPositions.get(message.target);
          const sourceX = source?.x ?? 48;
          const targetX = target?.x ?? sourceX;
          const isSelfMessage = sourceX === targetX;
          const interactionType = message.data?.interactionType ?? "message";
          const lineY = row.top + row.height - 20;
          const messagePath = isSelfMessage
            ? `M ${sourceX} ${lineY} H ${sourceX + 48} V ${lineY + 24} H ${sourceX + 3}`
            : `M ${sourceX} ${lineY} H ${targetX}`;
          const labelX = isSelfMessage ? sourceX + 24 : (sourceX + targetX) / 2;
          const classNames = [
            "sequence-message-line",
            `sequence-message-${interactionType}`,
            message.data?.candidate ? "candidate" : "",
          ].filter(Boolean).join(" ");
          const marker = interactionType === "return" ? markerId("return") : markerId("arrow");
          const displayLines = wrapSequenceText(sequenceTextWithData(message, t), Math.max(18, Math.min(34, Math.abs(sourceX - targetX) / 8)));
          return (
            <g key={message.id}>
              <title>{sequenceTextWithData(message, t)}</title>
              <text x={labelX} y={row.top + 11} textAnchor="middle" className="sequence-message-number">
                {formatNumber(number(message.data?.sequence, 0), locale)}
              </text>
              <path d={messagePath} className={classNames} markerEnd={`url(#${marker})`} />
              <text x={labelX} y={row.top + 24} textAnchor="middle" className="sequence-message-label">
                {displayLines.map((line, index) => (
                  <tspan key={`${message.id}-line-${index}`} x={labelX} dy={index === 0 ? 0 : 16}>{line}</tspan>
                ))}
              </text>
              {message.data?.fragmentLabel ? (
                <text x="38" y={row.top + row.height - 11} className="sequence-fragment-condition">
                  {message.data.fragmentLabel}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      <Panel
        className="react-flow__controls"
        position="bottom-left"
        role="group"
        aria-label={t("interview.process.sequenceControlsAria")}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <ControlButton className="react-flow__controls-zoomin" onClick={() => zoomBy(1.18)} aria-label={t("interview.process.zoomIn")} title={t("interview.process.zoomIn")}>
          <SequenceDiagramControlIcon type="zoomIn" />
        </ControlButton>
        <ControlButton className="react-flow__controls-zoomout" onClick={() => zoomBy(0.85)} aria-label={t("interview.process.zoomOut")} title={t("interview.process.zoomOut")}>
          <SequenceDiagramControlIcon type="zoomOut" />
        </ControlButton>
        <ControlButton className="react-flow__controls-fitview" onClick={fitViewport} aria-label={t("interview.process.fit")} title={t("interview.process.fit")}>
          <SequenceDiagramControlIcon type="fitView" />
        </ControlButton>
      </Panel>
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

function ProcessGraph({ graph, view, t, locale }: { graph: Graph | SequenceGraph; view: "flowchart" | "sequence"; t: Translate; locale: Parameters<typeof formatNumber>[1] }) {
  if (view === "sequence") {
    return <SequenceDiagram graph={graph as SequenceGraph} t={t} locale={locale} />;
  }

  return (
    <div className="process-flow-canvas">
      <ReactFlowProvider>
        <FlowchartCanvas graph={graph as FlowchartGraph} />
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
    setIsSendingCommand(true);
    setNotice("");
    try {
      await onEditProcessModel(instruction, baseProcessVersion, baseStateVersion);
      setNotice(t("interview.process.updated"));
    } catch (error) {
      setNotice(processModelErrorMessage(error, "command", t));
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
            <strong className="process-model-title">
              {t("interview.process.title")}
              <ProcessFlowInfo t={t} />
            </strong>
            <p>{t("interview.process.description")}</p>
          </div>
          <div className="process-model-header-actions">
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
        {graphReady ? <ProcessGraph graph={activeGraph} view={view} t={t} locale={locale} /> : (
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
                {graphReady ? <ProcessGraph graph={activeGraph} view={view} t={t} locale={locale} /> : (
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
