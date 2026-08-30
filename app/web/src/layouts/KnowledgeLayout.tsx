import { lazy, Suspense } from "react";
import { useI18n } from "../i18n";
import { KnowledgeSubNav } from "../features/knowledge/components/KnowledgeSubNav";
import { PageHeader } from "./PageHeader";
import type { KnowledgeLayoutProps } from "../types/pageProps";

const InterviewRecordPage = lazy(() => import("../pages/InterviewRecordPage").then(({ InterviewRecordPage: page }) => ({ default: page })));
const KnowledgeDocumentsPage = lazy(() => import("../pages/KnowledgeDocumentsPage").then(({ KnowledgeDocumentsPage: page }) => ({ default: page })));
const KnowledgeCollectionPage = lazy(() => import("../pages/KnowledgeCollectionPage").then(({ KnowledgeCollectionPage: page }) => ({ default: page })));
const KnowledgeListPage = lazy(() => import("../pages/KnowledgeListPage").then(({ KnowledgeListPage: page }) => ({ default: page })));
const KnowledgeInterviewPage = lazy(() => import("../pages/KnowledgeInterviewPage").then(({ KnowledgeInterviewPage: page }) => ({ default: page })));
const KnowledgeRecordsPage = lazy(() => import("../pages/KnowledgeRecordsPage").then(({ KnowledgeRecordsPage: page }) => ({ default: page })));
const KnowledgeSettingsPage = lazy(() => import("../pages/KnowledgeSettingsPage").then(({ KnowledgeSettingsPage: page }) => ({ default: page })));

export function KnowledgeLayout(props: KnowledgeLayoutProps) {
  const { t } = useI18n();
  if (props.route.name === "knowledge-dbs") {
    return (
      <Suspense fallback={<p className="empty" role="status">{t("common.loading")}</p>}>
        <KnowledgeListPage
          knowledges={props.knowledges}
          onNavigate={props.navigate}
          onOpenCreateKnowledge={props.onOpenCreateKnowledge}
          onOpenDashboard={props.user && ["admin", "knowledge_manager"].includes(props.user.role) ? () => props.navigate("/dashboard") : undefined}
          canManage={props.user?.role === "admin" || props.user?.role === "knowledge_manager"}
          isPreparingKnowledgeCreation={props.isPreparingKnowledgeCreation}
          knowledgeCreationError={props.knowledgeCreationError}
        />
      </Suspense>
    );
  }

  if (!props.selectedKnowledgeDb) {
    return <p className="empty">{t("navigation.knowledgeRequired")}</p>;
  }

  const canManageKnowledge = props.user?.role === "admin" || props.user?.role === "knowledge_manager";

  if (props.route.name === "knowledge-db" || props.route.name === "knowledge-new") {
    return (
      <Suspense fallback={<p className="empty" role="status">{t("common.loading")}</p>}>
        <KnowledgeCollectionPage {...props} />
      </Suspense>
    );
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
            aria-label={t("navigation.backToKnowledgeList")}
          >
            <span className="page-header-back-icon" aria-hidden="true">←</span>
            <span className="page-header-back-label">{t("navigation.backToKnowledgeList")}</span>
          </button>
        }
        actions={canManageKnowledge && props.route.name !== "knowledge-settings" ? (
          <button
            type="button"
            className="primary"
            aria-label={t("settings.title")}
            title={t("settings.title")}
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
      <Suspense fallback={<p className="empty" role="status">{t("common.loading")}</p>}>
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
      </Suspense>
    </>
  );
}
