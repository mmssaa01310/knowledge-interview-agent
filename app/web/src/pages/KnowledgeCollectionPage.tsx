import { useEffect, useState } from "react";
import { formatDate, formatNumber } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";
import { useI18n } from "../i18n";
import { OptionPicker } from "../components/ui/OptionPicker";

export function KnowledgeCollectionPage(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [description, setDescription] = useState("");
  const [createKnowledgeDbId, setCreateKnowledgeDbId] = useState(props.selectedKnowledgeDb?.id ?? "");
  const canManage = props.user?.role === "admin" || props.user?.role === "knowledge_manager";
  const isCreateDialogOpen = canManage && props.route.name === "knowledge-new";

  useEffect(() => {
    if (!isCreateDialogOpen || !props.selectedKnowledgeDb) return;
    setCreateKnowledgeDbId(props.selectedKnowledgeDb.id);
  }, [isCreateDialogOpen, props.selectedKnowledgeDb?.id]);

  if (!props.selectedKnowledgeDb) return null;

  const knowledgeDbPath = `/knowledge-dbs/${props.selectedKnowledgeDb.id}`;
  const currentKnowledges = props.knowledges.filter((knowledge) => knowledge.knowledgeDbId === props.selectedKnowledgeDb?.id);

  function closeDialog() {
    setName("");
    setPurpose("");
    setDescription("");
    props.navigate(knowledgeDbPath);
  }

  function submitKnowledge() {
    if (!name.trim()) return;
    props.onCreateKnowledge(
      {
        name: name.trim(),
        purpose: purpose.trim() || undefined,
        description: description.trim() || undefined
      },
      createKnowledgeDbId,
    );
    setName("");
    setPurpose("");
    setDescription("");
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>{t("knowledge.listTitle")}</h2>
        </div>
        {canManage ? (
          <button
            type="button"
            className="primary"
            onClick={() => props.navigate(`/knowledge-dbs/${props.selectedKnowledgeDb?.id}/knowledges/new`)}
          >
            {t("knowledge.createButton")}
          </button>
        ) : null}
      </div>
      {props.knowledgeCreationError ? <p className="notice error">{props.knowledgeCreationError}</p> : null}

      {isCreateDialogOpen ? (
        <div className="dialog-backdrop" role="presentation">
          <div className="dialog-panel" role="dialog" aria-modal="true" aria-label={t("knowledge.createDialog.title")}>
            <div className="dialog-header">
              <div>
                <h2>{t("knowledge.createDialog.title")}</h2>
                <p>{t("knowledge.createDialog.description")}</p>
              </div>
            </div>
            <div className="form-stack">
              <label>{t("knowledge.createDialog.name")}<input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder={t("knowledge.createDialog.namePlaceholder")} /></label>
              <label>{t("knowledge.createDialog.purpose")}<input value={purpose} onChange={(event) => setPurpose(event.target.value)} placeholder={t("knowledge.createDialog.purposePlaceholder")} /></label>
              <label>{t("knowledge.createDialog.descriptionField")}<textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder={t("knowledge.createDialog.descriptionPlaceholder")} /></label>
              {props.knowledgeDbs.length > 1 ? (
                <label>
                  <span>{t("knowledge.createDialog.businessArea")}</span>
                  <OptionPicker
                    value={createKnowledgeDbId}
                    options={props.knowledgeDbs.map((knowledgeDb) => ({ value: knowledgeDb.id, label: knowledgeDb.name }))}
                    onChange={setCreateKnowledgeDbId}
                    ariaLabel={t("knowledge.createDialog.businessArea")}
                    searchable={props.knowledgeDbs.length > 6}
                    searchPlaceholder={t("knowledge.createDialog.businessArea")}
                    emptyLabel={t("knowledge.emptyView")}
                  />
                </label>
              ) : null}
            </div>
            <div className="dialog-actions">
              <button className="ghost" onClick={closeDialog}>{t("common.cancel")}</button>
              <button className="primary" onClick={submitKnowledge} disabled={!name.trim()}>{t("knowledge.createDialog.submit")}</button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="table-list">
        <div className="table-row table-head">
          <span>{t("knowledge.table.name")}</span>
          <span>{t("knowledge.table.purpose")}</span>
          <span>{t("knowledge.table.recordCount")}</span>
          <span>{t("knowledge.table.documentCount")}</span>
          <span>{t("knowledge.table.updatedAt")}</span>
        </div>
        {currentKnowledges.length === 0 ? (
          <p className="empty">{canManage ? t("knowledge.emptyManage") : t("knowledge.emptyView")}</p>
        ) : currentKnowledges.map((knowledge) => (
          <button
            type="button"
            key={knowledge.id}
            className="table-row selectable"
            onClick={() => props.navigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`)}
          >
            <span><strong>{knowledge.name}</strong>{knowledge.description && <small>{knowledge.description}</small>}</span>
            <span>{knowledge.purpose ?? knowledge.category ?? "-"}</span>
            <span>{formatNumber(knowledge.recordCount ?? 0, locale)}</span>
            <span>{formatNumber(knowledge.documentCount ?? 0, locale)}</span>
            <span>{formatDate(knowledge.updatedAt, locale)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
