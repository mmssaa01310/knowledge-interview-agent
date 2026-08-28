import { PageHeader } from "./PageHeader";
import { InterviewRecordPage } from "../pages/InterviewRecordPage";
import { RecordsPage } from "../pages/RecordsPage";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function RecordsLayout(props: KnowledgeLayoutProps) {
  if (props.route.name === "records") {
    return <RecordsPage records={props.records} onNavigate={props.navigate} />;
  }

  if (props.route.name !== "record-detail") {
    return <p className="empty">記録を選択してください。</p>;
  }

  if (!props.selectedRecord) {
    return <p className="empty">記録を読み込んでいます。</p>;
  }

  return (
    <>
      <PageHeader
        title={props.selectedRecord.title}
        description={props.selectedRecord.knowledgeName}
        actions={
          <button className="ghost compact" type="button" onClick={() => props.navigate("/records")}>
            記録一覧
          </button>
        }
      />
      <InterviewRecordPage {...props} />
    </>
  );
}
