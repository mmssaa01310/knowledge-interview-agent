import { KnowledgeSubNav } from "../features/knowledge/components/KnowledgeSubNav";
import { PageHeader } from "./PageHeader";
import { InterviewRecordPage } from "../pages/InterviewRecordPage";
import { KnowledgeDocumentsPage } from "../pages/KnowledgeDocumentsPage";
import { KnowledgeCollectionPage } from "../pages/KnowledgeCollectionPage";
import { KnowledgeListPage } from "../pages/KnowledgeListPage";
import { KnowledgeInterviewPage } from "../pages/KnowledgeInterviewPage";
import { KnowledgeRecordsPage } from "../pages/KnowledgeRecordsPage";
import { KnowledgeSettingsPage } from "../pages/KnowledgeSettingsPage";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeLayout(props: KnowledgeLayoutProps) {
  if (props.route.name === "knowledge-dbs") {
    return (
      <KnowledgeListPage
        knowledges={props.knowledges}
        onNavigate={props.navigate}
        onOpenCreateKnowledge={props.onOpenCreateKnowledge}
        isPreparingKnowledgeCreation={props.isPreparingKnowledgeCreation}
        knowledgeCreationError={props.knowledgeCreationError}
      />
    );
  }

  if (!props.selectedKnowledgeDb) {
    return <p className="empty">ナレッジを作成または選択してください。</p>;
  }

  if (props.route.name === "knowledge-db" || props.route.name === "knowledge-new") {
    return <KnowledgeCollectionPage {...props} />;
  }

  if (!props.selectedKnowledge) {
    return <p className="empty">ナレッジを作成または選択してください。</p>;
  }

  return (
    <>
      <PageHeader
        title={props.selectedKnowledge.name}
        actions={props.route.name === "knowledge-settings" ? undefined : (
          <button
            type="button"
            className="primary"
            onClick={() => props.navigate(`/knowledge-dbs/${props.selectedKnowledgeDb?.id}/knowledges/${props.selectedKnowledge?.id}/settings`)}
          >
            インタビュー設定
          </button>
        )}
      />
      <KnowledgeSubNav
        knowledgeDbId={props.selectedKnowledgeDb.id}
        knowledgeId={props.selectedKnowledge.id}
        activePath={window.location.pathname}
        onNavigate={props.navigate}
      />
      {props.route.name === "knowledge-record-detail" ? (
        <InterviewRecordPage {...props} />
      ) : props.route.name === "knowledge-records" ? (
        <KnowledgeRecordsPage {...props} />
      ) : props.route.name === "knowledge-settings" ? (
        <KnowledgeSettingsPage {...props} />
      ) : props.route.name === "knowledge-documents" ? (
        <KnowledgeDocumentsPage {...props} />
      ) : (
        <KnowledgeInterviewPage {...props} />
      )}
    </>
  );
}
