import type { KnowledgeField } from "../../lib/api";
import { useI18n } from "../../i18n";

type KnowledgeFieldsSectionProps = {
  fields: KnowledgeField[];
};

export function KnowledgeFieldsSection({ fields }: KnowledgeFieldsSectionProps) {
  const { t } = useI18n();
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("settings.tabs.fields")}</p>
          <h2>{t("settings.fields.title")}</h2>
          <p className="lede">{t("settings.fields.description")}</p>
        </div>
        <button className="primary">{t("settings.fields.add")}</button>
      </div>
      <div className="table-list">
        <div className="table-row table-head field-row">
          <span>{t("knowledge.fields.order")}</span>
          <span>{t("knowledge.fields.name")}</span>
          <span>{t("knowledge.fields.inputType")}</span>
          <span>{t("knowledge.fields.required")}</span>
        </div>
        {fields.length === 0 ? (
          <p className="empty">{t("settings.fields.empty")}</p>
        ) : fields.map((field) => (
          <div key={`${field.displayOrder}-${field.name}`} className="table-row field-row">
            <span>{field.displayOrder}</span>
            <span>
              <strong>{field.name}</strong>
              <small>{field.description ?? t("settings.fields.noDetail")}</small>
            </span>
            <span>{field.inputType}</span>
            <span>{field.required ? t("common.required") : t("common.optional")}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
