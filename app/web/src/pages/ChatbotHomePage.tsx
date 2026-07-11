import { ChatbotChatPage } from "./ChatbotChatPage";
import type { ChatbotLayoutProps } from "../types/pageProps";

export function ChatbotHomePage(props: ChatbotLayoutProps) {
  return <ChatbotChatPage {...props} />;
}
