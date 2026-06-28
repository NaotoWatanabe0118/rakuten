"""
購入フロー E2E テストスクリプト。
BrowserSession でブラウザを起動・Cookie復元・ログイン後、
直接カートに追加して購入フローをテストする（DRY_RUN=True で注文確定はスキップ）。
"""
import json
import logging
import sys
import time
import uuid
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
    ],
)

from src.browser import BrowserSession
from src.config import load_config
from src.models import SearchItem
from src.notifier import Notifier
from src.purchaser import CART_URL, Purchaser
from src.state_manager import StateManager

config = load_config()
config.dry_run = True
config.headless = False

# state をリセット
state_path = Path("data/stock_state.json")
data = json.loads(state_path.read_text())
for k in data:
    data[k]["purchase_status"] = "none"
    data[k]["consecutive_errors"] = 0
    data[k]["last_purchase_attempt"] = None
state_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
state = StateManager(config.state_file)

item = SearchItem(
    id="item-11280156",
    name="サンプル商品A",
    itemid="11280156",
    shopid="217830",
    units=1,
)

cookies_path = Path("data/cookies.json")
cookies = json.loads(cookies_path.read_text()) if cookies_path.exists() else []

notifier = Notifier(config.discord_webhook_url)
purchaser = Purchaser(config, state, notifier)

logging.info("=== 購入フロー直接テスト開始 ===")

session_id = str(uuid.uuid4())
state.mark_purchase_started(item.id, session_id)

CART_ADD_URL = (
    f"https://ts.direct.step.rakuten.co.jp/rms/mall/cartAdd/"
    f"?itemid={item.itemid}&shopid={item.shopid}&units={item.units}"
)

browser = BrowserSession(config)
try:
    browser.start()

    # Cookie復元・ログイン確認
    purchaser._restore_cookies(browser.sb, cookies)
    purchaser._ensure_logged_in(browser.sb)
    logging.info("現在のURL: %s", browser.sb.get_current_url())

    # カートに追加（ブラウザ経由）
    logging.info("カートに追加します: %s", CART_ADD_URL)
    browser.sb.open(CART_ADD_URL)
    time.sleep(3)
    logging.info("カート追加後URL: %s", browser.sb.get_current_url())

    # カートページへ
    browser.sb.open(CART_URL)
    time.sleep(3)
    logging.info("カートページURL: %s", browser.sb.get_current_url())

    # 「購入手続き」ボタン待機
    found_proceed = False
    for _ in range(10):
        time.sleep(1)
        r = browser.sb.driver.execute_cdp_cmd("Runtime.evaluate", {
            "expression": "!!document.querySelector('button[aria-label=\"購入手続き\"]')",
            "returnByValue": True,
        })
        if r.get("result", {}).get("value"):
            found_proceed = True
            logging.info("購入手続きボタンを検出しました")
            break

    if not found_proceed:
        logging.error("購入手続きボタンが見つかりません。カートが空の可能性があります。")
        html = browser.sb.get_page_source()
        logging.warning("カートHTML: %s", html[:3000])
        state.update_item_state(item.id, purchase_status="none", session_id=None)
        sys.exit(1)

    # _proceed_to_checkout 以降を実行
    purchaser._proceed_to_checkout(browser.sb, item)
    purchaser._handle_delivery_modal(browser.sb, item)
    purchaser._log_confirm_button(browser.sb, item)

    current_url = browser.sb.get_current_url()
    logging.info("[DRY RUN] 注文確認ページ到達: %s", current_url)
    time.sleep(5)

    state.update_item_state(item.id, purchase_status="none", session_id=None)
    logging.info("=== テスト完了 (success=True) ===")

except Exception as e:
    logging.error("=== テスト失敗: %s ===", e, exc_info=True)
    state.update_item_state(item.id, purchase_status="none", session_id=None)
    sys.exit(1)
finally:
    browser.stop()
