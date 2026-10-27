"""Recover an existing administrator from a privileged server terminal."""
import getpass

from fastapi import HTTPException

from ai_interviewer_api.auth.accounts import TENANT_ID, _find_login, update_account
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.audit import write_audit_log


def main() -> None:
    store.ensure_schema()
    admins = [user for user in store.list("users", TENANT_ID) if user.get("active") and user.get("role") == "admin"]
    if not admins:
        raise SystemExit("有効なシステム管理者が見つかりません。")
    print("復旧可能な管理者:")
    for admin in admins:
        print(f"  {admin['loginId']} ({admin['displayName']})")
    login_id = input("復旧する管理者のログインID: ").strip()
    account = _find_login(login_id)
    if not account or account.get("role") != "admin":
        raise SystemExit("有効なシステム管理者が見つかりません。")
    password = getpass.getpass("新しいパスワード（12文字以上）: ")
    if password != getpass.getpass("新しいパスワード（確認）: "):
        raise SystemExit("パスワードが一致しません。")
    operator = UserContext("local-operator", TENANT_ID, "admin", "端末操作")
    try:
        update_account(account["id"], TENANT_ID, operator.user_id, password=password, require_admin_target=True)
    except HTTPException as error:
        raise SystemExit("管理者の復旧に失敗しました。対象とパスワードを確認してください。") from error
    write_audit_log(operator, "recover_password", "user", account["id"], {"method": "terminal", "operator": getpass.getuser(), "actorDisplayName": operator.display_name, "targetDisplayName": account["displayName"]})
    print("管理者のパスワードを更新しました。再ログインしてください。")


if __name__ == "__main__":
    main()
