import { KnowledgeSubNav } from "../features/knowledge/components/KnowledgeSubNav";
import { PageHeader } from "./PageHeader";
import { InterviewRecordPage } from "../pages/InterviewRecordPage";
import { KnowledgeDocumentsPage } from "../pages/KnowledgeDocumentsPage";
import { KnowledgeCollectionPage } from "../pages/KnowledgeCollectionPage";
import { KnowledgeListPage } from "../pages/KnowledgeListPage";
import { KnowledgeOverviewPage } from "../pages/KnowledgeOverviewPage";
import { KnowledgeRecordsPage } from "../pages/KnowledgeRecordsPage";
import { KnowledgeSettingsPage } from "../pages/KnowledgeSettingsPage";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeLayout(props: KnowledgeLayoutProps) {
  if (props.route.name === "knowledge-dbs") {
    return (
      <KnowledgeListPage
        knowledgeDbs={props.knowledgeDbs}
        onNavigate={props.navigate}
        createKnowledgeDbError={props.createKnowledgeDbError}
      />
    );
  }

  if (!props.selectedKnowledgeDb) {
    return <p className="empty">ナレッジDBを作成または選択してください。</p>;
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
        eyebrow={props.selectedKnowledgeDb.name}
        title={props.selectedKnowledge.name}
        description={props.selectedKnowledge.description}
        actions={
          <button
            className="ghost compact"
            onClick={() => props.navigate(`/knowledge-dbs/${props.selectedKnowledgeDb?.id}/knowledges/${props.selectedKnowledge?.id}/settings`)}
          >
            編集
          </button>
        }
      />
      <KnowledgeSubNav
        knowledgeDbId={props.selectedKnowledgeDb.id}
        knowledgeId={props.selectedKnowledge.id}
        activePath={window.location.pathname}
        onNavigate={props.navigate}
      />
      {props.route.name === "knowledge-settings" ? (
        <KnowledgeSettingsPage {...props} />
      ) : props.route.name === "knowledge-documents" ? (
        <KnowledgeDocumentsPage {...props} />
      ) : props.route.name === "knowledge-records" ? (
        <KnowledgeRecordsPage {...props} />
      ) : props.route.name === "record-detail" ? (
        <InterviewRecordPage {...props} />
      ) : (
        <KnowledgeOverviewPage {...props} />
      )}
    </>
  );
}
