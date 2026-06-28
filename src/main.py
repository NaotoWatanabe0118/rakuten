import json
import logging
import time
from pathlib import Path

from src.browser import BrowserSession
from src.config import load_config, setup_logging
from src.exceptions import ConfigError, LoginError, StockCheckError
from src.models import SearchItem
from src.notifier import Notifier
from src.purchaser import Purchaser
from src.session import get_session, load_cookies, refresh_session
from src.state_manager import StateManager
from src.stock_checker import check_stock, is_session_expired

logger = logging.getLogger(__name__)

_LOGIN_REFRESH_INTERVAL = 1800  # 30分ごとにログイン状態を確認


def load_searches(path: Path) -> list[SearchItem]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for entry in data:
        if not entry.get("enabled", True):
            continue
        items.append(
            SearchItem(
                id=entry["id"],
                name=entry["name"],
                itemid=str(entry["itemid"]),
                shopid=str(entry["shopid"]),
                units=int(entry.get("units", 1)),
                enabled=True,
                notes=entry.get("notes", ""),
            )
        )
    return items


def main() -> None:
    try:
        config = load_config()
    except ConfigError as e:
        print(f"設定エラー: {e}")
        raise SystemExit(1)

    setup_logging(config)
    logger.info("楽天市場自動購入ボットを起動します")

    state_manager = StateManager(config.state_file)
    notifier = Notifier(config.discord_webhook_url)
    purchaser = Purchaser(config, state_manager, notifier)

    # HTTP セッション（在庫チェック API 用）
    try:
        session = get_session(config)
    except LoginError as e:
        logger.error("初回ログインに失敗しました: %s", e)
        raise SystemExit(1)

    # ブラウザセッション（購入フロー用・常駐）
    cookies = load_cookies(config.cookies_file) or []
    browser = BrowserSession(config)
    browser.start()
    purchaser._restore_cookies(browser.sb, cookies)
    purchaser._ensure_logged_in(browser.sb)
    logger.info("ブラウザ起動・ログイン完了。在庫検知待機を開始します")

    last_login_check = time.time()

    logger.info("監視ループを開始します（ポーリング間隔: %d秒）", config.poll_interval_seconds)

    try:
        while True:
            state_manager.recover_stale_purchases(timeout_minutes=10)
            searches = load_searches(config.searches_file)

            for item in searches:
                item_state = state_manager.get_item_state(item.id)

                # 購入済みまたは進行中はスキップ
                if item_state.purchase_status in ("success", "in_progress"):
                    logger.debug("[%s] スキップ（purchase_status=%s）", item.id, item_state.purchase_status)
                    continue

                try:
                    result = check_stock(session, item)
                except StockCheckError as e:
                    state_manager.increment_errors(item.id)
                    errors = state_manager.get_item_state(item.id).consecutive_errors
                    logger.warning("[%s] 在庫チェック失敗 (errors=%d): %s", item.id, errors, e)
                    if errors >= 3:
                        notifier.notify_error(item, e)
                    continue

                state_manager.update_item_state(
                    item.id,
                    last_checked=result.checked_at,
                    in_stock=result.success,
                )
                state_manager.reset_errors(item.id)

                # セッション切れ検出 → HTTP セッションを再取得
                if is_session_expired(result):
                    logger.warning("セッションが切れました。再ログインします")
                    try:
                        session = refresh_session(config)
                        cookies = load_cookies(config.cookies_file) or []
                    except LoginError as e:
                        logger.error("再ログインに失敗しました: %s", e)
                    continue

                if not result.success:
                    continue

                # 在庫検知 → ブラウザの健全性を確認してから購入フロー実行
                logger.info("[%s] 在庫を検知しました！購入を開始します", item.id)
                notifier.notify_stock_detected(item, result)

                if not browser.is_alive():
                    logger.warning("ブラウザが応答しません。再起動します")
                    browser.stop()
                    browser.start()
                    cookies = load_cookies(config.cookies_file) or []
                    purchaser._restore_cookies(browser.sb, cookies)
                    purchaser._ensure_logged_in(browser.sb)
                    last_login_check = time.time()

                purchase_result = purchaser.purchase_with_sb(item, browser.sb)

                if purchase_result.success:
                    notifier.notify_purchase_complete(item, purchase_result)
                    logger.info("[%s] 購入が完了しました", item.id)
                else:
                    notifier.notify_purchase_failed(
                        item, purchase_result.failure_reason or "不明なエラー"
                    )

            # 定期ログイン確認（セッション維持）
            if time.time() - last_login_check >= _LOGIN_REFRESH_INTERVAL:
                logger.info("定期ログイン確認を実行します")
                purchaser._ensure_logged_in(browser.sb)
                last_login_check = time.time()

            time.sleep(config.poll_interval_seconds)

    finally:
        browser.stop()


if __name__ == "__main__":
    main()
