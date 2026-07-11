import { ChatbotSubNav } from "../features/chatbots/components/ChatbotSubNav";
import { PageHeader } from "./PageHeader";
import { ChatbotChatPage } from "../pages/ChatbotChatPage";
import { ChatbotListPage } from "../pages/ChatbotListPage";
import { ChatbotOverviewPage } from "../pages/ChatbotOverviewPage";
import { ChatbotReferenceSettingsPage } from "../pages/ChatbotReferenceSettingsPage";
import type { ChatbotLayoutProps } from "../types/pageProps";

export function ChatbotLayout(props: ChatbotLayoutProps) {
  if (props.route.name === "chatbots" && !props.route.chatbotId) {
    return <ChatbotListPage chatbots={props.chatbots} onNavigate={props.navigate} />;
  }

  return (
    <>
      <PageHeader
        eyebrow="Chatbot"
        title={props.selectedChatbot.name}
        description="承認済みナレッジと取り込み完了ドキュメントだけを根拠に回答するチャットを構成します。"
      />
      <ChatbotSubNav
        chatbotId={props.selectedChatbot.id}
        activePath={window.location.pathname}
        onNavigate={props.navigate}
      />
      {props.route.name === "chatbot-references" ? (
        <ChatbotReferenceSettingsPage {...props} />
      ) : props.route.name === "chatbot-chat" ? (
        <ChatbotChatPage {...props} />
      ) : (
        <ChatbotOverviewPage {...props} />
      )}
    </>
  );
}
