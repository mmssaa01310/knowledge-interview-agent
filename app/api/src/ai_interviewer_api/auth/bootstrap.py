"""Create the first administrator from an interactive terminal."""
from getpass import getpass

from ai_interviewer_api.auth.accounts import TENANT_ID, create_account
from ai_interviewer_api.repositories.store import store


def main() -> None:
    store.ensure_schema()
    if store.count("users"):
        raise SystemExit("ユーザーが既に存在するため、初期管理者を作成できません。")
    login_id = input("管理者のログインID: ").strip()
    display_name = input("管理者の表示名: ").strip()
    password = getpass("パスワード（12文字以上）: ")
    if password != getpass("パスワード（確認）: "):
        raise SystemExit("パスワードが一致しません。")
    with store.live_capture_lock("bootstrap-admin"):
        if store.count("users"):
            raise SystemExit("ユーザーが既に存在するため、初期管理者を作成できません。")
        create_account(login_id, display_name, "admin", password, tenant_id=TENANT_ID)
    print("管理者を作成しました。")


if __name__ == "__main__":
    main()
