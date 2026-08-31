import { useEffect, useMemo, useState } from "react";
import type { InterviewRecord } from "@ai-interviewer/shared-types";
import { formatDate, formatNumber } from "../lib/date";
import { useI18n } from "../i18n";
import { OptionPicker } from "../components/ui/OptionPicker";
import type { KnowledgeLayoutProps } from "../types/pageProps";

type RecordFilters = {
  assignee: string;
  status: InterviewRecord["status"] | "";
  updatedFrom: string;
  updatedTo: string;
};

const EMPTY_RECORD_FILTERS: RecordFilters = {
  assignee: "",
  status: "",
  updatedFrom: "",
  updatedTo: "",
};

const RECORD_STATUSES: readonly InterviewRecord["status"][] = [
  "draft",
  "in_progress",
  "submitted",
  "returned",
  "approved",
];

const UNASSIGNED_FILTER_VALUE = "__unassigned__";

function parseLocalDate(value: string) {
  if (!value) return null;
  const parts = value.split("-").map(Number);
  if (parts.length !== 3 || parts.some((part) => !Number.isInteger(part))) return null;
  const [year, month, day] = parts;
  const date = new Date(year, month - 1, day);
  if (
    date.getFullYear() !== year
    || date.getMonth() !== month - 1
    || date.getDate() !== day
  ) {
    return null;
  }
  return date;
}

function matchesUpdatedDate(record: InterviewRecord, updatedFrom: string, updatedTo: string) {
  if (!updatedFrom && !updatedTo) return true;
  const updatedAt = new Date(record.updatedAt).getTime();
  if (!Number.isFinite(updatedAt)) return false;

  const fromDate = parseLocalDate(updatedFrom);
  if (fromDate && updatedAt < fromDate.getTime()) return false;

  const toDate = parseLocalDate(updatedTo);
  if (toDate) {
    toDate.setDate(toDate.getDate() + 1);
    if (updatedAt >= toDate.getTime()) return false;
  }

  return true;
}

export function KnowledgeRecordsPage(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();
  const { selectedKnowledgeDb, selectedKnowledge } = props;
  const [recordFilters, setRecordFilters] = useState<RecordFilters>(EMPTY_RECORD_FILTERS);
  const [isRecordFilterOpen, setIsRecordFilterOpen] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const [deleteConfirmationText, setDeleteConfirmationText] = useState("");
  const deleteTarget = props.records.find((record) => record.id === deleteTargetId) ?? null;
  const deleteConfirmationPhrase = t("errors.recordDeleteConfirmationPhrase");

  function openDeleteDialog(recordId: string) {
    setDeleteConfirmationText("");
    setDeleteTargetId(recordId);
  }

  function closeDeleteDialog() {
    setDeleteConfirmationText("");
    setDeleteTargetId(null);
  }

  useEffect(() => {
    setRecordFilters(EMPTY_RECORD_FILTERS);
    setIsRecordFilterOpen(false);
  }, [selectedKnowledge?.id]);
  const hasActiveRecordFilters = Object.values(recordFilters).some(Boolean);
  const assigneeOptions = useMemo(() => {
    const assignees = [...new Set(props.records.map((record) => record.ownerUserId?.trim() || UNASSIGNED_FILTER_VALUE))]
      .sort((left, right) => left.localeCompare(right, locale));
    return [
      { value: "", label: t("knowledge.records.all") },
      ...assignees.map((assignee) => ({
        value: assignee,
        label: assignee === UNASSIGNED_FILTER_VALUE ? t("common.notSet") : assignee,
      })),
    ];
  }, [locale, props.records, t]);
  const filteredRecords = useMemo(() => props.records.filter((record) => {
    const recordAssignee = record.ownerUserId?.trim() || UNASSIGNED_FILTER_VALUE;
    return (
      (!recordFilters.assignee || recordAssignee === recordFilters.assignee)
      && (!recordFilters.status || record.status === recordFilters.status)
      && matchesUpdatedDate(record, recordFilters.updatedFrom, recordFilters.updatedTo)
    );
  }), [props.records, recordFilters]);
  const recordCountLabel = hasActiveRecordFilters
    ? t("knowledge.records.filteredCount", {
      filtered: formatNumber(filteredRecords.length, locale),
      total: formatNumber(props.records.length, locale),
    })
    : t("common.itemCount", { count: formatNumber(props.records.length, locale) });

  if (!selectedKnowledgeDb || !selectedKnowledge) return null;

  const basePath = `/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${selectedKnowledge.id}`;
  const isAdmin = props.user?.role === "admin";
  const isManagementUser = props.user?.role === "admin" || props.user?.role === "knowledge_manager";

  function updateRecordFilter<K extends keyof RecordFilters>(key: K, value: RecordFilters[K]) {
    setRecordFilters((current) => ({ ...current, [key]: value }));
  }

  function openRecord(recordId: string) {
    props.navigate(`${basePath}/records/${recordId}`);
  }

  function returnRecord(recordId: string) {
    const reviewNote = window.prompt(t("knowledge.records.reviewPrompt"), "");
    if (reviewNote?.trim()) {
      void props.onChangeRecordStatusForRecord(recordId, "returned", reviewNote.trim());
    }
  }

  function approveRecord(recordId: string) {
    if (window.confirm(t("knowledge.records.approvePrompt"))) {
      void props.onChangeRecordStatusForRecord(recordId, "approved");
    }
  }

  return (
    <section className="panel page-stack">
      <div className="panel-header">
        <div className="panel-header-title">
          <h2>{t("knowledge.records.title")}</h2>
          <span className="counter" aria-label={recordCountLabel}>{recordCountLabel}</span>
        </div>
      </div>

      {props.recordNotice ? <p className="notice" role="status">{props.recordNotice}</p> : null}

      <div className="records-filter-panel" aria-labelledby="records-filter-title">
        <div className="records-filter-heading">
          <button
            className="records-filter-toggle"
            type="button"
            aria-expanded={isRecordFilterOpen}
            aria-controls="records-filter-controls"
            onClick={() => setIsRecordFilterOpen((current) => !current)}
          >
            <strong id="records-filter-title">{t("knowledge.records.filterTitle")}</strong>
            <span className={isRecordFilterOpen ? "records-filter-toggle-icon open" : "records-filter-toggle-icon"} aria-hidden="true" />
          </button>
        </div>
        {isRecordFilterOpen ? (
          <div id="records-filter-controls">
            <div className="records-filter-grid">
              <label>
                <span>{t("knowledge.records.assignee")}</span>
                <OptionPicker
                  value={recordFilters.assignee}
                  options={assigneeOptions}
                  onChange={(value) => updateRecordFilter("assignee", value)}
                  ariaLabel={t("knowledge.records.assignee")}
                  searchable={assigneeOptions.length > 7}
                  searchPlaceholder={t("knowledge.records.assignee")}
                  emptyLabel={t("knowledge.records.all")}
                />
              </label>
              <label>
                <span>{t("knowledge.records.status")}</span>
                <OptionPicker
                  value={recordFilters.status}
                  options={[
                    { value: "", label: t("knowledge.records.all") },
                    ...RECORD_STATUSES.map((status) => ({
                      value: status,
                      label: t(`interview.status.${status}`),
                    })),
                  ]}
                  onChange={(value) => updateRecordFilter("status", value as RecordFilters["status"])}
                  ariaLabel={t("knowledge.records.status")}
                  emptyLabel={t("knowledge.records.all")}
                />
              </label>
              <label>
                <span>{t("knowledge.records.updatedFrom")}</span>
                <input
                  type="date"
                  value={recordFilters.updatedFrom}
                  max={recordFilters.updatedTo || undefined}
                  onChange={(event) => updateRecordFilter("updatedFrom", event.target.value)}
                  aria-label={t("knowledge.records.updatedFrom")}
                />
              </label>
              <label>
                <span>{t("knowledge.records.updatedTo")}</span>
                <input
                  type="date"
                  value={recordFilters.updatedTo}
                  min={recordFilters.updatedFrom || undefined}
                  onChange={(event) => updateRecordFilter("updatedTo", event.target.value)}
                  aria-label={t("knowledge.records.updatedTo")}
                />
              </label>
            </div>
          </div>
        ) : null}
        {isRecordFilterOpen || hasActiveRecordFilters ? (
          <div className="records-filter-actions">
            <button
              className="ghost compact"
              type="button"
              onClick={() => setRecordFilters(EMPTY_RECORD_FILTERS)}
              disabled={!hasActiveRecordFilters}
            >
              {t("knowledge.records.reset")}
            </button>
          </div>
        ) : null}
      </div>

      <div className="table-list" data-guide="knowledge-records">
        <div className="table-row table-head records-workspace-row knowledge-records-row">
          <span>{t("knowledge.records.record")}</span>
          <span>{t("knowledge.records.assignee")}</span>
          <span>{t("knowledge.records.status")}</span>
          <span>{t("knowledge.records.updatedAt")}</span>
          <span>{t("knowledge.records.operation")}</span>
        </div>
        {props.records.length === 0 ? (
          <p className="empty">{t("knowledge.records.empty")}</p>
        ) : filteredRecords.length === 0 ? (
          <p className="empty">{t("knowledge.records.noMatch")}</p>
        ) : filteredRecords.map((record) => (
          <div
            className="table-row records-workspace-row knowledge-records-row"
            key={record.id}
            data-guide="record-item"
            data-record-path={`${basePath}/records/${record.id}`}
          >
            <span>
              <strong>{record.title}</strong>
              <small>{record.targetEquipment || record.targetProcess || "-"}</small>
            </span>
            <span>{record.ownerUserId || t("common.notSet")}</span>
            <span>
              <span className={record.status === "approved" ? "status-pill" : "status-pill muted"}>
                {t(`interview.status.${record.status}`)}
              </span>
            </span>
            <span>{formatDate(record.updatedAt, locale)}</span>
            <span className="inline-actions">
              <button className="ghost compact" type="button" data-guide="record-open" onClick={() => openRecord(record.id)}>
                {props.user?.role === "viewer" ? t("knowledge.records.viewerAction") : t("knowledge.records.editAction")}
              </button>
              {isAdmin ? (
                <button className="danger compact" type="button" onClick={() => openDeleteDialog(record.id)}>
                  {t("knowledge.records.delete")}
                </button>
              ) : null}
              {isManagementUser && record.status === "submitted" ? (
                <>
                  <button className="ghost compact" type="button" onClick={() => returnRecord(record.id)}>
                    {t("knowledge.records.return")}
                  </button>
                  <button className="primary compact" type="button" data-guide="knowledge-confirm" onClick={() => approveRecord(record.id)}>
                    {t("knowledge.records.approve")}
                  </button>
                </>
              ) : null}
            </span>
          </div>
        ))}
      </div>
      {deleteTarget ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDeleteDialog();
          }}
        >
          <div
            className="dialog-panel record-delete-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="record-delete-title"
          >
            <div className="dialog-header">
              <div>
                <h2 id="record-delete-title">{t("errors.recordDeleteTitle")}</h2>
                <p>{t("errors.recordDeleteConfirm", { title: deleteTarget.title })}</p>
              </div>
            </div>
            <label className="delete-confirmation-field">
              <span>{t("errors.recordDeleteVerification", { phrase: deleteConfirmationPhrase })}</span>
              <input
                type="text"
                value={deleteConfirmationText}
                onChange={(event) => setDeleteConfirmationText(event.target.value)}
                placeholder={deleteConfirmationPhrase}
                autoComplete="off"
                autoFocus
              />
            </label>
            <div className="dialog-actions">
              <button type="button" className="ghost" onClick={closeDeleteDialog}>
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="danger"
                disabled={deleteConfirmationText !== deleteConfirmationPhrase}
                onClick={() => {
                  const recordId = deleteTarget.id;
                  closeDeleteDialog();
                  void props.onDeleteRecord(recordId);
                }}
              >
                {t("common.delete")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
