import type { AppSection } from "../types/app";
import type { Route } from "./routeTypes";

export function parseRoute(pathname: string): Route {
  const segments = pathname.split("/").filter(Boolean);
  if (segments[0] === "login") return { name: "login" };
  if (segments[0] === "settings") return { name: "settings" };
  if (segments[0] === "chatbots") {
    if (segments[1] && segments[2] === "chat") return { name: "chatbot-chat", chatbotId: segments[1] };
    if (segments[1] && segments[2] === "references") return { name: "chatbot-references", chatbotId: segments[1] };
    if (segments[1]) return { name: "chatbot-overview", chatbotId: segments[1] };
    return { name: "chatbots", chatbotId: segments[1] };
  }
  if (segments[0] === "knowledge" && !segments[1]) return { name: "knowledge-dbs" };
  if (segments[0] === "knowledge-dbs" || segments[0] === "knowledge") {
    if (!segments[1]) return { name: "knowledge-dbs" };
    if (segments[2] === "knowledges" && segments[3] === "new") {
      return { name: "knowledge-new", knowledgeDbId: segments[1] };
    }
    if (segments[2] === "knowledges" && segments[3]) {
      if (segments[4] === "settings") {
        return { name: "knowledge-settings", knowledgeDbId: segments[1], knowledgeId: segments[3] };
      }
      if (segments[4] === "documents") {
        return { name: "knowledge-documents", knowledgeDbId: segments[1], knowledgeId: segments[3] };
      }
      if (segments[4] === "records" && segments[5]) {
        return { name: "record-detail", knowledgeDbId: segments[1], knowledgeId: segments[3], recordId: segments[5] };
      }
      if (segments[4] === "records") {
        return { name: "knowledge-records", knowledgeDbId: segments[1], knowledgeId: segments[3] };
      }
      return { name: "knowledge", knowledgeDbId: segments[1], knowledgeId: segments[3] };
    }
    return { name: "knowledge-db", knowledgeDbId: segments[1] };
  }
  return { name: "knowledge-dbs" };
}

export function routeSection(route: Route): AppSection {
  if (route.name === "settings") return "settings";
  return route.name.startsWith("chatbot") || route.name === "chatbots" ? "chatbots" : "knowledge";
}

export function getRouteKnowledgeDbId(route: Route): string | undefined {
  return "knowledgeDbId" in route ? route.knowledgeDbId : undefined;
}

export function getRouteKnowledgeId(route: Route): string | undefined {
  return "knowledgeId" in route ? route.knowledgeId : undefined;
}
