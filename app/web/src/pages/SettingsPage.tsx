import { useEffect, useState } from "react";
import type { UserRole } from "@ai-interviewer/shared-types";
import { OptionPicker } from "../components/ui/OptionPicker";
import { useI18n } from "../i18n";
import { ApiError, createUser, deleteUser, listUserAudit, listUsersPage, updateUser, type ManagedUser, type ManagedUserPage, type UserAuditPage, type UserProfile } from "../lib/api";
import { formatDate, formatNumber } from "../lib/date";

const roles: UserRole[] = ["admin", "knowledge_manager", "interviewer", "viewer"];
const pageSize = 20;
const emptyPage: ManagedUserPage = { items: [], total: 0, activeCount: 0, adminCount: 0 };

export function SettingsPage({ user: currentUser }: { user: UserProfile | null }) {
  const { t, locale } = useI18n();
  const [tab, setTab] = useState<"users" | "audit">("users");
  const [page, setPage] = useState<ManagedUserPage>(emptyPage);
  const [audit, setAudit] = useState<UserAuditPage>({ items: [], total: 0 });
  const [searchInput, setSearchInput] = useState("");
  const [roleInput, setRoleInput] = useState("");
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [auditOffset, setAuditOffset] = useState(0);
  const [auditAction, setAuditAction] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [auditLoading, setAuditLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ManagedUser | null>(null);
  const [loginId, setLoginId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<UserRole>("interviewer");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (!currentUser || currentUser.role !== "admin") return;
    let active = true;
    setLoading(true);
    listUsersPage(search, roleFilter, pageSize, offset).then((result) => {
      if (!active) return;
      if (offset > 0 && result.items.length === 0 && result.total <= offset) {
        setOffset(Math.max(0, Math.ceil(result.total / pageSize) - 1) * pageSize);
        return;
      }
      setPage(result);
    }).catch(() => { if (active) setError(t("common.account.operationFailed")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [currentUser?.userId, currentUser?.role, search, roleFilter, offset, revision, t]);

  useEffect(() => {
    if (!currentUser || currentUser.role !== "admin" || tab !== "audit") return;
    let active = true;
    setAuditLoading(true);
    listUserAudit(auditAction, pageSize, auditOffset).then((result) => { if (active) setAudit(result); })
      .catch(() => { if (active) setError(t("common.account.operationFailed")); })
      .finally(() => { if (active) setAuditLoading(false); });
    return () => { active = false; };
  }, [currentUser?.userId, currentUser?.role, tab, auditAction, auditOffset, revision, t]);

  function openForm(account?: ManagedUser) {
    setEditing(account ?? null);
    setLoginId(account?.loginId ?? "");
    setDisplayName(account?.displayName ?? "");
    setRole(account?.role ?? "interviewer");
    setPassword(""); setPasswordConfirm(""); setFormError("");
    setFormOpen(true);
  }

  function errorText(errorValue: unknown) {
    if (errorValue instanceof ApiError && errorValue.detail === "login_id_exists") return t("common.account.loginIdExists");
    if (errorValue instanceof ApiError && errorValue.detail === "last_admin_required") return t("common.account.lastAdmin");
    if (errorValue instanceof ApiError && errorValue.detail === "cannot_delete_self") return t("common.account.cannotDeleteSelf");
    return t("common.account.operationFailed");
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if ((!editing || password) && password !== passwordConfirm) {
      setFormError(t("common.account.management.passwordMismatch"));
      return;
    }
    setBusy(true); setFormError("");
    try {
      if (editing) {
        await updateUser(editing.id, { displayName, ...(loginId !== editing.loginId ? { loginId } : {}), ...(role !== editing.role ? { role } : {}), ...(password ? { password } : {}) });
      } else {
        await createUser({ loginId, displayName, role, password });
      }
      setFormOpen(false); setOffset(0); setRevision((value) => value + 1);
    } catch (errorValue) { setFormError(errorText(errorValue)); }
    finally { setBusy(false); }
  }

  async function remove() {
    if (busy || !deleteTarget) return;
    setBusy(true); setError("");
    try {
      await deleteUser(deleteTarget.id);
      setDeleteTarget(null); setRevision((value) => value + 1);
    } catch (errorValue) { setError(errorText(errorValue)); setDeleteTarget(null); }
    finally { setBusy(false); }
  }

  const roleOptions = roles.map((value) => ({ value, label: t(`navigation.roles.${value}`) }));
  const passwordMismatch = Boolean(passwordConfirm && password !== passwordConfirm);
  const shownOffset = tab === "users" ? offset : auditOffset;
  const shownTotal = tab === "users" ? page.total : audit.total;
  const shownLoading = tab === "users" ? loading : auditLoading;
  const changeOffset = tab === "users" ? setOffset : setAuditOffset;

  return (
    <section className="panel account-management">
      <header className="account-management-header">
        <div><p className="eyebrow">{t("common.settings")}</p><h2>{t("common.account.users")}</h2><p>{t("common.account.management.subtitle")}</p></div>
        <button className="primary" type="button" onClick={() => openForm()} disabled={!currentUser || currentUser.role !== "admin"}>{t("common.account.addUser")}</button>
      </header>
      <div className="account-summary" aria-label={t("common.account.management.summary")}>
        <div><span>{t("common.account.management.activeUsers")}</span><strong>{formatNumber(page.activeCount, locale)}</strong></div>
        <div><span>{t("common.account.management.adminUsers")}</span><strong>{formatNumber(page.adminCount, locale)}</strong></div>
      </div>
      <div className="dashboard-tabs account-tabs" role="tablist" aria-label={t("common.account.users")}>
        <button id="account-tab-users" type="button" role="tab" aria-controls="account-tabpanel" aria-selected={tab === "users"} className="dashboard-tab" onClick={() => { setTab("users"); setError(""); }}>{t("common.account.management.usersTab")}</button>
        <button id="account-tab-audit" type="button" role="tab" aria-controls="account-tabpanel" aria-selected={tab === "audit"} className="dashboard-tab" onClick={() => { setTab("audit"); setError(""); }}>{t("common.account.management.auditTab")}</button>
      </div>
      {error ? <p role="alert" className="account-error">{error}</p> : null}
      <div id="account-tabpanel" role="tabpanel" aria-labelledby={`account-tab-${tab}`} className="account-tabpanel">
      {tab === "users" ? <>
        <form className="dashboard-filters account-filters" onSubmit={(event) => { event.preventDefault(); setOffset(0); setSearch(searchInput.trim()); setRoleFilter(roleInput); }}>
          <label className="account-search"><span>{t("common.search")}</span><input type="search" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder={t("common.account.management.searchPlaceholder")} /></label>
          <label><span>{t("common.account.role")}</span><OptionPicker value={roleInput} options={[{ value: "", label: t("common.account.management.allRoles") }, ...roleOptions]} onChange={setRoleInput} ariaLabel={t("common.account.role")} /></label>
          <div className="dashboard-filter-actions"><button className="primary" type="submit">{t("common.search")}</button><button className="ghost" type="button" onClick={() => { setSearchInput(""); setRoleInput(""); setSearch(""); setRoleFilter(""); setOffset(0); }}>{t("common.account.management.clearFilters")}</button></div>
        </form>
        <p className="account-result-count">{t("common.account.management.results", { count: formatNumber(page.total, locale) })}</p>
        <div className="table-list account-table" role="table" aria-label={t("common.account.management.usersTab")}>
          <div className="table-row table-head account-user-table-row" role="row"><span role="columnheader">{t("common.account.displayName")}</span><span role="columnheader">{t("common.account.loginId")}</span><span role="columnheader">{t("common.account.role")}</span><span role="columnheader">{t("common.operation")}</span></div>
          {loading ? <p className="empty" role="status">{t("common.loading")}</p> : page.items.length === 0 ? <p className="empty">{t("common.account.management.noUsers")}</p> : page.items.map((account) => {
            const self = account.id === currentUser?.userId;
            const lastAdmin = account.role === "admin" && page.adminCount <= 1;
            const blocked = self || lastAdmin;
            const reason = self ? t("common.account.cannotDeleteSelf") : lastAdmin ? t("common.account.lastAdmin") : undefined;
            return <div className="table-row account-user-table-row" role="row" key={account.id}>
              <span role="cell" data-label={t("common.account.displayName")}><strong>{account.displayName}</strong>{self ? <small>{t("common.account.management.you")}</small> : null}</span>
              <span role="cell" className="account-id" data-label={t("common.account.loginId")}>{account.loginId}</span><span role="cell" data-label={t("common.account.role")}><span className="status-pill muted">{t(`navigation.roles.${account.role}`)}</span></span>
              <span role="cell" className="account-row-actions" data-label={t("common.operation")}><span className="inline-actions"><button className="ghost compact" type="button" onClick={() => openForm(account)} disabled={busy}>{t("common.edit")}</button><button className="danger compact" type="button" title={reason} aria-label={reason ? `${t("common.delete")}：${reason}` : t("common.delete")} onClick={() => setDeleteTarget(account)} disabled={busy || !currentUser || blocked}>{t("common.delete")}</button></span>{reason ? <small>{reason}</small> : null}</span>
            </div>;
          })}
        </div>
      </> : <>
        <p className="account-audit-note">{t("common.account.management.auditScope")}</p>
        <div className="dashboard-filters account-filters"><label><span>{t("common.account.management.action")}</span><OptionPicker value={auditAction} options={[{ value: "", label: t("common.account.management.allActions") }, ...["create", "update", "delete", "recover_password"].map((value) => ({ value, label: t(`common.account.management.actions.${value}`) }))]} onChange={(value) => { setAuditAction(value); setAuditOffset(0); }} ariaLabel={t("common.account.management.action")} /></label></div>
        <div className="table-list account-table" role="table" aria-label={t("common.account.management.auditTab")}>
          <div className="table-row table-head account-audit-table-row" role="row"><span role="columnheader">{t("common.account.management.date")}</span><span role="columnheader">{t("common.account.management.actor")}</span><span role="columnheader">{t("common.account.management.target")}</span><span role="columnheader">{t("common.account.management.action")}</span></div>
          {auditLoading ? <p className="empty" role="status">{t("common.loading")}</p> : audit.items.length === 0 ? <p className="empty">{t("common.account.management.noAudit")}</p> : audit.items.map((event) => <div className="table-row account-audit-table-row" role="row" key={event.id}>
            <span role="cell" data-label={t("common.account.management.date")}>{formatDate(event.createdAt, locale, currentUser?.timezone ?? undefined)}</span>
            <span role="cell" data-label={t("common.account.management.actor")}><strong>{event.actorName}</strong><small>{event.actorUserId}</small></span>
            <span role="cell" data-label={t("common.account.management.target")}><strong>{event.targetName}</strong><small>{event.targetUserId}</small></span>
            <span role="cell" data-label={t("common.account.management.action")}><strong>{["create", "update", "delete", "recover_password"].includes(event.action) ? t(`common.account.management.actions.${event.action}`) : event.action}</strong>{event.role ? <small>{t(`navigation.roles.${event.role}`)}</small> : null}{event.fields.length ? <small>{event.fields.map((field) => t(`common.account.management.fields.${field}`)).join("、")}</small> : null}<small>{event.result === "success" ? t("common.account.management.auditSuccess") : event.result}</small></span>
          </div>)}
        </div>
      </>}
      <div className="account-pagination"><span>{t("common.account.management.range", { start: formatNumber(shownTotal ? shownOffset + 1 : 0, locale), end: formatNumber(Math.min(shownOffset + pageSize, shownTotal), locale), total: formatNumber(shownTotal, locale) })}</span><div className="inline-actions"><button className="ghost compact" type="button" disabled={shownLoading || shownOffset === 0} onClick={() => changeOffset(Math.max(0, shownOffset - pageSize))}>{t("common.account.management.previous")}</button><button className="ghost compact" type="button" disabled={shownLoading || shownOffset + pageSize >= shownTotal} onClick={() => changeOffset(shownOffset + pageSize)}>{t("common.account.management.next")}</button></div></div>
      </div>
      {formOpen ? <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (!busy && event.target === event.currentTarget) setFormOpen(false); }}>
        <div className="dialog-panel account-dialog" role="dialog" aria-modal="true" aria-labelledby="account-form-title">
          <div className="dialog-header"><h2 id="account-form-title">{editing ? t("common.account.editUser") : t("common.account.addUser")}</h2><p>{t("common.account.management.formDescription")}</p></div>
          <form className="account-form" onSubmit={(event) => void submit(event)}>
            <label>{t("common.account.loginId")}<input autoComplete="off" autoFocus required maxLength={128} value={loginId} onChange={(event) => setLoginId(event.target.value)} /></label>
            <label>{t("common.account.displayName")}<input required maxLength={128} value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
            <label><span>{t("common.account.role")}</span><OptionPicker value={role} options={roleOptions} onChange={(value) => setRole(value as UserRole)} ariaLabel={t("common.account.role")} disabled={Boolean(editing?.role === "admin" && page.adminCount <= 1)} />{editing?.role === "admin" && page.adminCount <= 1 ? <small className="form-help">{t("common.account.lastAdmin")}</small> : null}</label>
            <label>{editing ? t("common.account.newPassword") : t("common.account.password")}<input type="password" autoComplete="new-password" required={!editing} minLength={12} maxLength={128} value={password} onChange={(event) => { setPassword(event.target.value); if (!event.target.value) setPasswordConfirm(""); setFormError(""); }} /></label>
            {(!editing || password) ? <label>{t("common.account.management.confirmPassword")}<input type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={passwordConfirm} aria-invalid={passwordMismatch} onChange={(event) => { setPasswordConfirm(event.target.value); setFormError(""); }} /></label> : null}
            <p className="form-help">{passwordMismatch ? t("common.account.management.passwordMismatch") : t("common.account.passwordHint")}</p>
            {editing && currentUser && editing.id === currentUser.userId && (Boolean(password) || loginId !== editing.loginId || role !== editing.role) ? <p className="form-help">{t("common.account.management.reloginNotice")}</p> : null}
            {formError ? <p className="account-error" role="alert">{formError}</p> : null}
            <div className="dialog-actions"><button className="ghost" type="button" onClick={() => setFormOpen(false)} disabled={busy}>{t("common.cancel")}</button><button className="primary" type="submit" disabled={busy || passwordMismatch}>{busy ? t("common.saving") : editing ? t("common.save") : t("common.create")}</button></div>
          </form>
        </div>
      </div> : null}
      {deleteTarget ? <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (!busy && event.target === event.currentTarget) setDeleteTarget(null); }}><div className="dialog-panel account-dialog" role="dialog" aria-modal="true" aria-labelledby="account-delete-title"><div className="dialog-header"><h2 id="account-delete-title">{t("common.account.management.deleteTitle")}</h2><p>{t("common.account.deleteConfirm", { name: deleteTarget.displayName })}</p></div><div className="dialog-actions"><button className="ghost" type="button" onClick={() => setDeleteTarget(null)} disabled={busy}>{t("common.cancel")}</button><button className="danger" type="button" onClick={() => void remove()} disabled={busy}>{t("common.delete")}</button></div></div></div> : null}
    </section>
  );
}
