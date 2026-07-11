import { useState } from "react";
import type { Knowledge, KnowledgeDb } from "@ai-interviewer/shared-types";
import { createLocalChatbot } from "../features/chatbots/api/chatbotApi";
import type { DocumentSummary } from "../features/documents/api/documentApi";
import { answerChat } from "../lib/api";
import type { ChatbotLayoutProps } from "../types/pageProps";
import type { Chatbot, ChatMessage, ChatMessageEvidence } from "../types/app";
import type { Route } from "./routeTypes";

const DEFAULT_CHAT_MODEL_ID = "global.amazon.nova-2-lite-v1:0";

type UseChatbotControllerArgs = {
  route: Route;
  navigate: (path: string) => void;
  knowledgeDbs: KnowledgeDb[];
  knowledges: Knowledge[];
  documents: DocumentSummary[];
};

export function useChatbotController(args: UseChatbotControllerArgs) {
  const [chatbots, setChatbots] = useState<Chatbot[]>([
    {
      id: "chatbot-main",
      name: "保全ナレッジ参照チャット",
      referenceKnowledgeDbIds: [],
      referenceKnowledgeIds: [],
      referenceDocumentIds: [],
      excludedDocumentIds: [],
      modelId: DEFAULT_CHAT_MODEL_ID,
      searchLimit: 5,
      confidenceThreshold: 0.7
    }
  ]);
  const [chatbotInput, setChatbotInput] = useState("");
  const [chatbotMessages, setChatbotMessages] = useState<ChatMessage[]>([
    { role: "ai", text: "承認済みナレッジと取り込み済みドキュメントを参照して回答します。" }
  ]);

  const selectedChatbotId = "chatbotId" in args.route ? args.route.chatbotId : undefined;
  const selectedChatbot = selectedChatbotId
    ? chatbots.find((chatbot) => chatbot.id === selectedChatbotId) ?? chatbots[0]
    : chatbots[0];

  function handleCreateChatbot() {
    const chatbot = createLocalChatbot(`新規チャットボット ${chatbots.length + 1}`);
    setChatbots((items) => [...items, chatbot]);
    args.navigate(`/chatbots/${chatbot.id}/references`);
  }

  async function handleSendChatbotMessage() {
    if (!chatbotInput.trim()) return;
    const content = chatbotInput.trim();
    const knowledgeEvidence: ChatMessageEvidence[] = args.knowledges
      .filter((knowledge) => selectedChatbot.referenceKnowledgeIds.includes(knowledge.id))
      .map((knowledge) => ({
        type: "knowledge",
        title: knowledge.name,
        detail: `${knowledge.category ?? knowledge.purpose ?? "未分類"} / 記録 ${knowledge.recordCount} 件`,
        status: knowledge.status
      }));
    const documentEvidence: ChatMessageEvidence[] = args.documents
      .filter((doc) => selectedChatbot.referenceDocumentIds.includes(doc.id))
      .filter((doc) => !selectedChatbot.excludedDocumentIds.includes(doc.id))
      .map((doc) => ({
        type: "document",
        title: doc.fileName,
        detail: `${doc.ingestionStatus === "completed" ? "取り込み完了" : doc.ingestionStatus} / 進捗 ${doc.progressPercent}%`,
        status: doc.ingestionStatus
      }));
    const evidences = [...knowledgeEvidence, ...documentEvidence].filter((item) => item.status === undefined || item.status === "active" || item.status === "completed");

    setChatbotInput("");
    setChatbotMessages((messages) => [
      ...messages,
      { role: "user", text: content }
    ]);

    try {
      const response = await answerChat(selectedChatbot.id, {
        content,
        modelId: selectedChatbot.modelId,
        referenceKnowledgeDbIds: selectedChatbot.referenceKnowledgeDbIds,
        referenceKnowledgeIds: selectedChatbot.referenceKnowledgeIds,
        referenceDocumentIds: selectedChatbot.referenceDocumentIds,
        excludedDocumentIds: selectedChatbot.excludedDocumentIds,
        searchLimit: selectedChatbot.searchLimit,
        confidenceThreshold: selectedChatbot.confidenceThreshold
      });
      setChatbotMessages((messages) => [
        ...messages,
        {
          role: "ai",
          text: response.answer,
          evidences: response.citations.length
            ? response.citations.map((citation) => ({
                type: citation.startsWith("文書:") ? "document" : "knowledge",
                title: citation,
                detail: "Bedrock回答の参照元"
              }))
            : evidences
        }
      ]);
    } catch (error) {
      console.error("Failed to answer chatbot message", error);
      setChatbotMessages((messages) => [
        ...messages,
        {
          role: "ai",
          text: "チャット回答を生成できませんでした。API接続またはBedrock設定を確認してください。",
          evidences
        }
      ]);
    }
  }

  function updateChatbotReferences(next: Partial<Chatbot>) {
    if (!selectedChatbot) return;
    setChatbots((items) => items.map((chatbot) => (
      chatbot.id === selectedChatbot.id ? { ...chatbot, ...next } : chatbot
    )));
  }

  const chatbotLayoutProps: ChatbotLayoutProps = {
    route: args.route,
    chatbots,
    selectedChatbot,
    knowledgeDbs: args.knowledgeDbs,
    knowledges: args.knowledges,
    documents: args.documents,
    chatbotInput,
    setChatbotInput,
    chatbotMessages,
    navigate: args.navigate,
    onCreateChatbot: handleCreateChatbot,
    onSendChatbotMessage: handleSendChatbotMessage,
    onUpdateReferences: updateChatbotReferences
  };

  return {
    chatbots,
    selectedChatbot,
    handleCreateChatbot,
    chatbotLayoutProps
  };
}