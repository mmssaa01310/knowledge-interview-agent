import { useEffect, useMemo, useState } from "react";
import { formatDate, formatNumber } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";
import { useI18n } from "../i18n";
import { OptionPicker, type OptionPickerOption } from "../components/ui/OptionPicker";
import { buildKnowledgeTagOptions } from "../features/knowledge/tagOptions";

export function KnowledgeCollectionPage(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [createKnowledgeDbId, setCreateKnowledgeDbId] = useState(props.selectedKnowledgeDb?.id ?? "");
  const [tagOperationError, setTagOperationError] = useState(false);
  const canManage = props.user?.role === "admin" || props.user?.role === "knowledge_manager";
  const isCreateDialogOpen = canManage && props.route.name === "knowledge-new";
  const tagOptions = useMemo(
    () => buildKnowledgeTagOptions(props.availableTags, props.knowledges, locale, t("common.notSet")),
    [props.availableTags, props.knowledges, locale, t],
  );

  useEffect(() => {
    if (!isCreateDialogOpen || !props.selectedKnowledgeDb) return;
    setCreateKnowledgeDbId(props.selectedKnowledgeDb.id);
  }, [isCreateDialogOpen, props.selectedKnowledgeDb?.id]);

  if (!props.selectedKnowledgeDb) return null;

  const knowledgeDbPath = `/knowledge-dbs/${props.selectedKnowledgeDb.id}`;
  const currentKnowledges = props.knowledges.filter((knowledge) => knowledge.knowledgeDbId === props.selectedKnowledgeDb?.id);

  function deleteTag(option: OptionPickerOption) {
    if (!option.id || !window.confirm(t("settings.tags.deleteConfirm", { tag: option.label }))) return;
    setTagOperationError(false);
    void props.onDeleteTag(option.id).catch((error) => {
      console.error("Failed to delete knowledge tag", error);
      setTagOperationError(true);
    });
  }

  function closeDialog() {
    setName("");
    setPurpose("");
    setDescription("");
    setTags([]);
    props.navigate(knowledgeDbPath);
  }

  function submitKnowledge() {
    if (!name.trim()) return;
    props.onCreateKnowledge(
      {
        name: name.trim(),
        purpose: purpose.trim() || undefined,
        description: description.trim() || undefined,
        tags,
      },
      createKnowledgeDbId,
    );
    setName("");
    setPurpose("");
    setDescription("");
    setTags([]);
  }

  return (
    <section className="panel" data-guide="knowledge-list">
      <div className="panel-header">
        <div>
          <h2>{t("knowledge.listTitle")}</h2>
        </div>
        {canManage ? (
          <button
            type="button"
            className="primary"
            data-guide="knowledge-create"
            onClick={() => props.navigate(`/knowledge-dbs/${props.selectedKnowledgeDb?.id}/knowledges/new`)}
          >
            {t("knowledge.createButton")}
          </button>
        ) : null}
      </div>
      {props.knowledgeCreationError ? <p className="notice error">{props.knowledgeCreationError}</p> : null}

      {isCreateDialogOpen ? (
        <div className="dialog-backdrop" role="presentation">
          <div className="dialog-panel" data-guide="knowledge-create-form" role="dialog" aria-modal="true" aria-label={t("knowledge.createDialog.title")}>
            <div className="dialog-header">
              <div>
                <h2>{t("knowledge.createDialog.title")}</h2>
                <p>{t("knowledge.createDialog.description")}</p>
              </div>
            </div>
            <div className="form-stack">
              <div className="knowledge-create-primary-fields">
                <label>
                  <span>{t("knowledge.createDialog.name")}</span>
                  <input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder={t("knowledge.createDialog.namePlaceholder")} />
                </label>
                <label className="knowledge-tag-field">
                  <span>{t("settings.tags.title")}</span>
                  <OptionPicker
                    value={tags[0] ?? ""}
                    options={tagOptions}
                    onChange={(tag) => {
                      const normalizedTag = tag.trim().replace(/^#+/, "");
                      setTags(normalizedTag ? [normalizedTag] : []);
                    }}
                    ariaLabel={t("settings.tags.inputAria")}
                    placeholder={t("settings.tags.placeholder")}
                    searchPlaceholder={t("settings.tags.placeholder")}
                    emptyLabel={t("common.notSet")}
                    searchable
                    creatable
                    onCreateOption={props.onCreateTag}
                    createOptionLabel={(tag) => t("settings.tags.create", { tag: `#${tag.trim().replace(/^#+/, "")}` })}
                    selectedValueLabel={(tag) => `#${tag.trim().replace(/^#+/, "")}`}
                    showOptionActions={(option) => canManage && Boolean(option.id)}
                    onUpdateOption={(option, tag) => option.id ? props.onUpdateTag(option.id, tag) : Promise.resolve()}
                    onDeleteOption={deleteTag}
                    editOptionLabel={(option) => t("settings.tags.editAria", { tag: option.label })}
                    deleteOptionLabel={(option) => t("settings.tags.deleteAria", { tag: option.label })}
                    editOptionInputLabel={() => t("settings.tags.editLabel")}
                    saveOptionLabel={t("common.save")}
                    cancelOptionEditLabel={t("common.cancel")}
                    optionUpdateErrorLabel={t("settings.tags.operationFailed")}
                    className="knowledge-tag-picker"
                  />
                </label>
                {tagOperationError ? <p className="notice error">{t("settings.tags.operationFailed")}</p> : null}
              </div>
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
        <div className="table-row table-head knowledge-collection-row">
          <span>{t("knowledge.table.name")}</span>
          <span>{t("knowledge.table.tags")}</span>
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
            className="table-row selectable knowledge-collection-row"
            data-guide="knowledge-item"
            data-knowledge-path={`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`}
            onClick={() => props.navigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`)}
          >
            <span>
              <strong>{knowledge.name}</strong>
              {knowledge.description && <small>{knowledge.description}</small>}
            </span>
            <span className="knowledge-table-tags">
              {knowledge.tags?.length ? (
                <span className="knowledge-tag-list" aria-label={t("knowledge.tagsLabel")}>
                  {knowledge.tags.map((tag) => <span className="knowledge-tag" key={tag}>#{tag}</span>)}
                </span>
              ) : <span className="knowledge-table-empty">{t("knowledge.tagsNotSet")}</span>}
            </span>
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
