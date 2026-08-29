import { useI18n } from "../i18n";
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
  const { t } = useI18n();
  if (props.route.name === "knowledge-dbs") {
    return (
      <KnowledgeListPage
        knowledges={props.knowledges}
        onNavigate={props.navigate}
        onOpenCreateKnowledge={props.onOpenCreateKnowledge}
        canManage={props.user?.role === "admin" || props.user?.role === "knowledge_manager"}
        isPreparingKnowledgeCreation={props.isPreparingKnowledgeCreation}
        knowledgeCreationError={props.knowledgeCreationError}
      />
    );
  }

  if (!props.selectedKnowledgeDb) {
    return <p className="empty">{t("navigation.knowledgeRequired")}</p>;
  }

  const canManageKnowledge = props.user?.role === "admin" || props.user?.role === "knowledge_manager";

  if (props.route.name === "knowledge-db" || props.route.name === "knowledge-new") {
    return <KnowledgeCollectionPage {...props} />;
  }

  if (!props.selectedKnowledge) {
    return <p className="empty">{t("navigation.knowledgeRequired")}</p>;
  }

  if (!canManageKnowledge && (props.route.name === "knowledge-settings" || props.route.name === "knowledge-documents")) {
    return <p className="empty">{t("errors.permissionDenied")}</p>;
  }

  const knowledgeBasePath = `/knowledge-dbs/${props.selectedKnowledgeDb.id}/knowledges/${props.selectedKnowledge.id}`;
  const activeKnowledgePath = props.route.name === "knowledge-record-detail"
    ? `${knowledgeBasePath}/interview`
    : window.location.pathname;

  return (
    <>
      <PageHeader
        title={props.selectedKnowledge.name}
        backAction={
          <button
            type="button"
            className="page-header-back"
            onClick={() => props.navigate("/knowledge-dbs")}
          >
            ← {t("navigation.backToKnowledgeList")}
          </button>
        }
        actions={canManageKnowledge && props.route.name !== "knowledge-settings" ? (
          <button
            type="button"
            className="primary"
            onClick={() => props.navigate(`/knowledge-dbs/${props.selectedKnowledgeDb?.id}/knowledges/${props.selectedKnowledge?.id}/settings`)}
          >
            {t("settings.title")}
          </button>
        ) : undefined}
      />
      <KnowledgeSubNav
        knowledgeDbId={props.selectedKnowledgeDb.id}
        knowledgeId={props.selectedKnowledge.id}
        activePath={activeKnowledgePath}
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
