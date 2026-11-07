import { useEffect, useState } from "react";
import { ApiError, checkOpenAIConnection, deleteOpenAISettings, getOpenAISettings, listApplicationSettingsAdmins, saveOpenAISettings, setApplicationSettingsAdmin, type ApplicationSettingsAdmin, type OpenAISettingsStatus } from "../lib/api";
import { useI18n } from "../i18n";

const emptyStatus: OpenAISettingsStatus = { configured: false, maskedApiKey: null, updatedAt: null };

function formatUpdatedAt(value: string) {
  const parts = new Intl.DateTimeFormat("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(value));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}/${values.month}/${values.day} ${values.hour}:${values.minute}`;
}

export function ApplicationSettingsPanel() {
  const { t } = useI18n();
  const [status, setStatus] = useState(emptyStatus);
  const [admins, setAdmins] = useState<ApplicationSettingsAdmin[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyAdminId, setBusyAdminId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [connectionDialogOpen, setConnectionDialogOpen] = useState(false);

  async function reload() {
    const [nextStatus, nextAdmins] = await Promise.all([
      getOpenAISettings(),
      listApplicationSettingsAdmins(),
    ]);
    setStatus(nextStatus);
    setAdmins(nextAdmins.items);
  }

  useEffect(() => {
    let active = true;
    Promise.all([getOpenAISettings(), listApplicationSettingsAdmins()])
      .then(([nextStatus, nextAdmins]) => {
        if (!active) return;
        setStatus(nextStatus);
        setAdmins(nextAdmins.items);
      })
      .catch(() => { if (active) setError(t("common.applicationSettings.operationFailed")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [t]);

  function errorMessage(errorValue: unknown) {
    if (errorValue instanceof ApiError) {
      if (errorValue.detail === "openai_api_key_connection_failed") {
        return t("common.applicationSettings.connectionFailed");
      }
      if (errorValue.detail === "openai_encryption_key_unavailable") {
        return t("common.applicationSettings.encryptionUnavailable");
      }
      if (errorValue.detail === "last_application_settings_admin") {
        return t("common.applicationSettings.lastAdmin");
      }
      if (errorValue.detail === "application_settings_admin_target_must_be_admin") {
        return t("common.applicationSettings.adminTargetRequired");
      }
    }
    return t("common.applicationSettings.operationFailed");
  }

  async function checkConnection() {
    if (!apiKey.trim() || busy) return;
    setBusy(true); setError(""); setNotice(""); setConnectionDialogOpen(false);
    try {
      await checkOpenAIConnection(apiKey.trim());
      setConnectionDialogOpen(true);
    } catch (errorValue) {
      setError(errorMessage(errorValue));
    } finally {
      setBusy(false);
    }
  }

  async function saveKey(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!apiKey.trim() || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const nextStatus = await saveOpenAISettings(apiKey.trim());
      setStatus(nextStatus);
      setApiKey("");
      setNotice(t("common.applicationSettings.saved"));
    } catch (errorValue) {
      setError(errorMessage(errorValue));
    } finally {
      setBusy(false);
    }
  }

  async function removeKey() {
    if (busy || !window.confirm(t("common.applicationSettings.deleteConfirm"))) return;
    setBusy(true); setError(""); setNotice("");
    try {
      setStatus(await deleteOpenAISettings());
      setApiKey("");
      setNotice(t("common.applicationSettings.deleted"));
    } catch (errorValue) {
      setError(errorMessage(errorValue));
    } finally {
      setBusy(false);
    }
  }

  async function changeAdmin(admin: ApplicationSettingsAdmin) {
    if (busyAdminId) return;
    setBusyAdminId(admin.id); setError(""); setNotice("");
    try {
      await setApplicationSettingsAdmin(admin.id, !admin.applicationSettingsAdmin);
      await reload();
      setNotice(t("common.applicationSettings.adminsSaved"));
    } catch (errorValue) {
      setError(errorMessage(errorValue));
    } finally {
      setBusyAdminId(null);
    }
  }

  const settingsAdminCount = admins.filter((admin) => admin.applicationSettingsAdmin).length;

  return (
    <div className="application-settings-panel">
      <section className="settings-section">
        <header className="account-management-header">
          <div>
            <p className="eyebrow">{t("common.applicationSettings.eyebrow")}</p>
            <h3>{t("common.applicationSettings.openaiTitle")}</h3>
            <p>{t("common.applicationSettings.openaiDescription")}</p>
          </div>
          <span className={`status-pill ${status.configured ? "confirmed" : "muted"}`}>
            {status.configured ? t("common.applicationSettings.configured") : t("common.applicationSettings.notConfigured")}
          </span>
        </header>
        {loading ? <p className="empty" role="status">{t("common.loading")}</p> : (
          <>
            {status.configured ? (
              <div className="account-form">
                <div className="configured-key-field">
                  <div className="configured-key-heading">
                    <span className="configured-key-label">{t("common.applicationSettings.configuredKey")}</span>
                    {status.updatedAt ? (
                      <span className="configured-key-updated">
                        {t("common.applicationSettings.updatedAt", { date: formatUpdatedAt(status.updatedAt) })}
                      </span>
                    ) : null}
                  </div>
                  <input
                    type="text"
                    value={status.maskedApiKey ?? "********"}
                    readOnly
                    autoComplete="off"
                    spellCheck={false}
                    aria-label={t("common.applicationSettings.configuredKey")}
                  />
                </div>
              </div>
            ) : null}
            <form className="account-form" onSubmit={(event) => void saveKey(event)}>
              <label>
                {t("common.applicationSettings.apiKey")}
                <input
                  type="password"
                  autoComplete="new-password"
                  value={apiKey}
                  onChange={(event) => { setApiKey(event.target.value); setError(""); setNotice(""); }}
                  placeholder={status.configured ? t("common.applicationSettings.replaceKey") : t("common.applicationSettings.enterKey")}
                  maxLength={512}
                  spellCheck={false}
                />
              </label>
              <div className="inline-actions">
                <button className="ghost" type="button" onClick={() => void checkConnection()} disabled={!apiKey.trim() || busy}>
                  {busy ? t("common.applicationSettings.checking") : t("common.applicationSettings.checkConnection")}
                </button>
                <button className="primary" type="submit" disabled={!apiKey.trim() || busy}>
                  {busy ? t("common.saving") : t("common.applicationSettings.saveKey")}
                </button>
                {status.configured ? <button className="danger" type="button" onClick={() => void removeKey()} disabled={busy}>{t("common.applicationSettings.removeKey")}</button> : null}
              </div>
            </form>
          </>
        )}
      </section>

      <section className="settings-section">
        <header className="account-management-header">
          <div>
            <h3>{t("common.applicationSettings.adminsTitle")}</h3>
            <p>{t("common.applicationSettings.adminsDescription")}</p>
          </div>
          <span className="status-pill muted">{t("common.applicationSettings.adminCount", { count: settingsAdminCount })}</span>
        </header>
        <div className="table-list account-table" role="table" aria-label={t("common.applicationSettings.adminsTitle")}>
          <div className="table-row app-settings-admin-row table-head" role="row">
            <span role="columnheader">{t("common.account.displayName")}</span>
            <span role="columnheader">{t("common.account.loginId")}</span>
            <span role="columnheader">{t("common.applicationSettings.settingsAdmin")}</span>
          </div>
          {loading ? <p className="empty" role="status">{t("common.loading")}</p> : admins.map((admin) => (
            <div className="table-row app-settings-admin-row" role="row" key={admin.id}>
              <span role="cell" data-label={t("common.account.displayName")}><strong>{admin.displayName}</strong></span>
              <span role="cell" className="account-id" data-label={t("common.account.loginId")}>{admin.loginId}</span>
              <span role="cell" data-label={t("common.applicationSettings.settingsAdmin")}>
                <label className="inline-checkbox">
                  <input
                    type="checkbox"
                    checked={admin.applicationSettingsAdmin}
                    onChange={() => void changeAdmin(admin)}
                    disabled={Boolean(busyAdminId) || (admin.applicationSettingsAdmin && settingsAdminCount <= 1)}
                  />
                  <span>{admin.applicationSettingsAdmin ? t("common.applicationSettings.enabled") : t("common.applicationSettings.disabled")}</span>
                </label>
              </span>
            </div>
          ))}
        </div>
      </section>

      {notice ? <p className="notice" role="status">{notice}</p> : null}
      {error ? <p className="account-error" role="alert">{error}</p> : null}
      {connectionDialogOpen ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setConnectionDialogOpen(false);
          }}
        >
          <div
            className="dialog-panel account-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="gpt-live-connection-success-title"
          >
            <div className="dialog-header">
              <h2 id="gpt-live-connection-success-title">
                {t("common.applicationSettings.connectionSucceededTitle")}
              </h2>
              <p>{t("common.applicationSettings.connectionSucceeded")}</p>
            </div>
            <div className="dialog-actions">
              <button
                className="primary"
                type="button"
                autoFocus
                onClick={() => setConnectionDialogOpen(false)}
              >
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
