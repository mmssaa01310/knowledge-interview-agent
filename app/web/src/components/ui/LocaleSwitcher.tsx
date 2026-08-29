import { useI18n } from "../../i18n";

export function LocaleSwitcher() {
  const { locale, locales, setLocale, t } = useI18n();

  return (
    <label className="locale-switcher">
      <span>{t("common.language")}</span>
      <select
        value={locale}
        onChange={(event) => setLocale(event.target.value as typeof locale)}
        aria-label={t("common.selectLanguage")}
      >
        {locales.map((option) => (
          <option key={option.code} value={option.code}>{option.name}</option>
        ))}
      </select>
    </label>
  );
}
