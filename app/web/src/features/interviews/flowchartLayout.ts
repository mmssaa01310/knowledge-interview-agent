import type { Edge, Node } from "@xyflow/react";

export type FlowchartNodeKind =
  | "start"
  | "activity"
  | "decision"
  | "end"
  | "system"
  | "data"
  | "subprocess";

export type FlowchartGraph = {
  nodes: Node[];
  edges: Edge[];
};

async function createElk() {
  const { default: Elk } = await import("elkjs/lib/elk.bundled.js");
  return new Elk();
}

let elkInstance: Awaited<ReturnType<typeof createElk>> | null = null;

async function getElk() {
  elkInstance ??= await createElk();
  return elkInstance;
}

const flowchartLayoutOptions = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.padding": "24",
  "elk.spacing.nodeNode": "48",
  "elk.spacing.edgeNode": "28",
  "elk.layered.spacing.nodeNodeBetweenLayers": "72",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.nodePlacement.favorStraightEdges": "true",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
};

function normalizedNodeType(value: unknown): FlowchartNodeKind | null {
  if (
    value === "start"
    || value === "activity"
    || value === "decision"
    || value === "end"
    || value === "system"
    || value === "data"
    || value === "subprocess"
  ) {
    return value;
  }
  return null;
}

function labelMatches(label: string, pattern: RegExp) {
  return pattern.test(label.toLocaleLowerCase());
}

export function resolveFlowchartNodeType(nodeType: unknown, label: string): FlowchartNodeKind {
  const explicitType = normalizedNodeType(nodeType);
  // `activity` is the backend default, so refine it from an unambiguous label
  // when the model did not provide a more specific flowchart shape.
  if (explicitType && explicitType !== "activity") return explicitType;

  const normalizedLabel = label.trim();
  if (labelMatches(normalizedLabel, /(?:開始|スタート)(?:する|します)?\s*[。．.!！]?$/u)) {
    return "start";
  }
  if (labelMatches(normalizedLabel, /(?:終了|完了)(?:する|します|となる|すること)?\s*[。．.!！]?$/u)) {
    return "end";
  }
  if (labelMatches(normalizedLabel, /取得|出力|表示|入力|読込|読み込|書き出|ダウンロード|export|download|display|output|input/u)) {
    return "data";
  }
  if (labelMatches(normalizedLabel, /判定|確認|場合|なら|条件|分岐|判断|if|check|decision/u)) {
    return "decision";
  }
  if (labelMatches(normalizedLabel, /サブプロセス|subprocess/u)) {
    return "subprocess";
  }
  if (labelMatches(normalizedLabel, /システム|system/u)) {
    return "system";
  }
  return "activity";
}

export function flowchartShape(nodeType: FlowchartNodeKind) {
  if (nodeType === "start" || nodeType === "end") return "terminator";
  if (nodeType === "decision") return "decision";
  if (nodeType === "data") return "data";
  if (nodeType === "subprocess") return "subprocess";
  if (nodeType === "system") return "system";
  return "process";
}

function visualLength(value: string) {
  return Array.from(value).reduce(
    (length, character) => length + (character.charCodeAt(0) > 0xff ? 1.8 : 1),
    0,
  );
}

export function flowchartNodeDimensions(label: string, nodeType: FlowchartNodeKind) {
  const longestLine = Math.max(
    ...label.split(/\r?\n/u).map((line) => visualLength(line)),
    1,
  );
  const width = Math.min(300, Math.max(190, Math.ceil(146 + longestLine * 7.5)));
  const contentLineCapacity = Math.max(12, Math.floor((width - 48) / 11));
  const lineCount = Math.max(1, Math.ceil(longestLine / contentLineCapacity));
  const baseHeight = nodeType === "decision" ? 124 : nodeType === "start" || nodeType === "end" ? 76 : 72;
  const height = Math.max(baseHeight, 34 + lineCount * 21);
  return { width, height };
}

function nodeDimension(node: Node, key: "width" | "height", fallback: number) {
  const value = node.style?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function fallbackFlowchartLayout(graph: FlowchartGraph): FlowchartGraph {
  let y = 24;
  const nodes = graph.nodes.map((node) => {
    const next = {
      ...node,
      position: { x: 24, y },
    };
    y += nodeDimension(node, "height", 72) + 72;
    return next;
  });
  return { ...graph, nodes };
}

export async function layoutFlowchart(graph: FlowchartGraph): Promise<FlowchartGraph> {
  if (graph.nodes.length === 0) return graph;

  const layoutEngine = await getElk();
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const result = await layoutEngine.layout({
    id: "flowchart-root",
    layoutOptions: flowchartLayoutOptions,
    children: graph.nodes.map((node) => ({
      id: node.id,
      width: nodeDimension(node, "width", 190),
      height: nodeDimension(node, "height", 72),
    })),
    edges: graph.edges
      .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
      .map((edge) => ({
        id: edge.id,
        sources: [edge.source],
        targets: [edge.target],
      })),
  });
  const positions = new Map(
    (result.children ?? []).map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]),
  );

  return {
    ...graph,
    nodes: graph.nodes.map((node) => ({
      ...node,
      position: positions.get(node.id) ?? node.position,
    })),
  };
}
