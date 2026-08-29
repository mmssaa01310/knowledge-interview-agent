import { useI18n } from "../i18n";

export function SettingsPage() {
  const { t } = useI18n();
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>{t("common.settings")}</h2>
        </div>
      </div>
      <p className="empty">{t("common.preparing")}</p>
    </section>
  );
}
