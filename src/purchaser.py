import logging
import re
import time
import uuid
from pathlib import Path

from seleniumbase import SB

from src.config import Config
from src.exceptions import CheckoutError, LoginError
from src.models import PurchaseResult, SearchItem
from src.notifier import Notifier
from src.state_manager import StateManager

logger = logging.getLogger(__name__)

CART_URL = "https://cart.step.rakuten.co.jp/cart/"
LOGIN_URL = "https://grp01.id.rakuten.co.jp/rms/nid/login"
LOGIN_DOMAIN = "grp01.id.rakuten.co.jp"
SSO_DOMAIN = "login.account.rakuten.com"

# カートページから注文確認ページへ進むボタンのセレクタ（優先順）
_PROCEED_SELECTORS = [
    'button[aria-label="購入手続き"]',
    'input[value*="ご注文手続き"]',
    'a[href*="checkout"]',
    '.btn-order',
    '#proceedCheckout',
]

# 注文確定ボタンのセレクタ（優先順）
_CONFIRM_SELECTORS = [
    'button[aria-label="注文を確定する"]',
    'button[aria-label*="注文を確定"]',
    'input[value*="注文を確定"]',
    'input[value*="購入する"]',
    'button[id*="confirm"]',
    '.btn-order-confirm',
    'input[type="submit"][value*="確定"]',
]

_ORDER_NUMBER_PATTERN = re.compile(r'注文番号[:\s：]*([0-9\-]+)')


class Purchaser:
    def __init__(
        self, config: Config, state_manager: StateManager, notifier: Notifier | None = None
    ) -> None:
        self._config = config
        self._state = state_manager
        self._notifier = notifier

    def _notify_step_error(self, item: SearchItem, step: str, error: Exception) -> None:
        logger.error("[%s] %s でエラー: %s", item.id, step, error)
        if self._notifier:
            try:
                self._notifier.notify_step_error(item, step, error)
            except Exception as ne:
                logger.warning("Discord通知失敗: %s", ne)

    def purchase(self, item: SearchItem, cookies: list[dict]) -> PurchaseResult:
        session_id = str(uuid.uuid4())
        self._state.mark_purchase_started(item.id, session_id)
        if self._config.dry_run:
            logger.info("[DRY RUN][%s] 購入フロー開始 (session=%s)", item.id, session_id)
        else:
            logger.info("[%s] 購入フロー開始 (session=%s)", item.id, session_id)

        try:
            result = self._run_flow(item, session_id, cookies)
        except Exception as e:
            logger.error("[%s] 購入フロー失敗: %s", item.id, e, exc_info=True)
            self._state.mark_purchase_failed(item.id)
            return PurchaseResult(
                item_id=item.id,
                session_id=session_id,
                success=False,
                failure_reason=str(e),
            )

        if self._config.dry_run:
            # ドライランは state を none に戻して次回も監視できるようにする
            self._state.update_item_state(
                item.id, purchase_status="none", session_id=None
            )
        else:
            self._state.mark_purchase_complete(item.id)
        return result

    def _run_flow(
        self, item: SearchItem, session_id: str, cookies: list[dict]
    ) -> PurchaseResult:
        with SB(
            uc=True,
            headless=self._config.headless,
            user_data_dir=self._config.chrome_user_data_dir,
            chromium_arg="--no-sandbox --disable-dev-shm-usage",
        ) as sb:
            sb.driver.set_page_load_timeout(self._config.selenium_page_timeout)

            try:
                self._restore_cookies(sb, cookies)
            except Exception as e:
                self._notify_step_error(item, "Cookie復元", e)
                raise

            try:
                self._ensure_logged_in(sb)
            except Exception as e:
                self._notify_step_error(item, "ログイン確認", e)
                raise

            try:
                self._go_to_cart(sb, item)
            except Exception as e:
                self._notify_step_error(item, "カートページ遷移", e)
                raise

            try:
                self._proceed_to_checkout(sb, item)
            except Exception as e:
                self._notify_step_error(item, "注文手続きへ進む", e)
                raise

            try:
                self._handle_delivery_modal(sb, item)
            except Exception as e:
                self._notify_step_error(item, "配送日時モーダル処理", e)
                raise

            if self._config.dry_run:
                self._log_confirm_button(sb, item)
                current_url = sb.get_current_url()
                logger.info(
                    "[DRY RUN][%s] 注文確認ページに到達しました。購入はスキップします。URL: %s",
                    item.id,
                    current_url,
                )
                time.sleep(1)
                return PurchaseResult(
                    item_id=item.id,
                    session_id=session_id,
                    success=True,
                    order_number=None,
                )

            try:
                order_number = self._confirm_order(sb, item)
            except Exception as e:
                self._notify_step_error(item, "注文確定", e)
                raise

        logger.info("[%s] 購入完了 (注文番号: %s)", item.id, order_number)
        return PurchaseResult(
            item_id=item.id,
            session_id=session_id,
            success=True,
            order_number=order_number,
        )

    def purchase_with_sb(self, item: SearchItem, sb) -> PurchaseResult:
        """事前起動済みブラウザを使って購入フローを実行（ブラウザ起動・Cookie復元・ログインをスキップ）。"""
        session_id = str(uuid.uuid4())
        self._state.mark_purchase_started(item.id, session_id)
        if self._config.dry_run:
            logger.info("[DRY RUN][%s] 購入フロー開始 (prewarmed, session=%s)", item.id, session_id)
        else:
            logger.info("[%s] 購入フロー開始 (prewarmed, session=%s)", item.id, session_id)

        try:
            result = self._run_flow_with_sb(item, session_id, sb)
        except Exception as e:
            logger.error("[%s] 購入フロー失敗: %s", item.id, e, exc_info=True)
            self._state.mark_purchase_failed(item.id)
            return PurchaseResult(
                item_id=item.id,
                session_id=session_id,
                success=False,
                failure_reason=str(e),
            )

        if self._config.dry_run:
            self._state.update_item_state(item.id, purchase_status="none", session_id=None)
        else:
            self._state.mark_purchase_complete(item.id)
        return result

    def _run_flow_with_sb(self, item: SearchItem, session_id: str, sb) -> PurchaseResult:
        """事前起動済みブラウザでカート→購入確定まで実行する（呼び出し元がログイン済みを保証すること）。"""
        try:
            self._go_to_cart(sb, item)
        except Exception as e:
            self._notify_step_error(item, "カートページ遷移", e)
            raise

        try:
            self._proceed_to_checkout(sb, item)
        except Exception as e:
            self._notify_step_error(item, "注文手続きへ進む", e)
            raise

        try:
            self._handle_delivery_modal(sb, item)
        except Exception as e:
            self._notify_step_error(item, "配送日時モーダル処理", e)
            raise

        if self._config.dry_run:
            self._log_confirm_button(sb, item)
            current_url = sb.get_current_url()
            logger.info(
                "[DRY RUN][%s] 注文確認ページに到達しました。購入はスキップします。URL: %s",
                item.id, current_url,
            )
            time.sleep(1)
            return PurchaseResult(
                item_id=item.id,
                session_id=session_id,
                success=True,
                order_number=None,
            )

        try:
            order_number = self._confirm_order(sb, item)
        except Exception as e:
            self._notify_step_error(item, "注文確定", e)
            raise

        logger.info("[%s] 購入完了 (注文番号: %s)", item.id, order_number)
        return PurchaseResult(
            item_id=item.id,
            session_id=session_id,
            success=True,
            order_number=order_number,
        )

    def _log_confirm_button(self, sb, item: SearchItem) -> None:
        """DRY RUN 時に注文確定ボタンの検出結果をログ出力する（クリックしない）"""
        btn = self._cdp_visible_button(sb, "注文を確定する")
        if btn:
            logger.info(
                "[DRY RUN][%s] 注文を確定するボタンを検出: 座標 (%.1f, %.1f)",
                item.id, btn["x"], btn["y"],
            )
            return
        for sel in _CONFIRM_SELECTORS:
            if sb.is_element_present(sel):
                logger.info(
                    "[DRY RUN][%s] 注文を確定するボタンを検出 (セレクタ: %s)",
                    item.id, sel,
                )
                return
        logger.warning("[DRY RUN][%s] 注文を確定するボタンが見つかりません", item.id)

    def _restore_cookies(self, sb, cookies: list[dict]) -> None:
        # CDP経由でCookieを設定（WebDriver接続不安定時も動作する）
        sb.open("https://www.rakuten.co.jp/")
        for c in cookies:
            try:
                params = {k: v for k, v in c.items()
                          if k in ("name", "value", "domain", "path", "httpOnly", "secure")}
                if "name" in params and "value" in params:
                    sb.driver.execute_cdp_cmd("Network.setCookie", params)
            except Exception as exc:
                logger.debug(
                    "Cookie設定スキップ (%s @ %s): %s",
                    c.get("name"),
                    c.get("domain"),
                    exc,
                )

    def _ensure_logged_in(self, sb) -> None:
        # Cookie設定後にリロードしてセッションを反映する
        sb.open("https://www.rakuten.co.jp/")
        # JS リダイレクトのトリガーを待つ最小限の猶予
        time.sleep(0.5)
        if LOGIN_DOMAIN not in sb.get_current_url():
            logger.info("セッション有効（ログイン済み）")
            return

        # ログインフォームへの入力
        logger.info("ログインが必要です")
        sb.open(LOGIN_URL)
        time.sleep(0.5)

        if LOGIN_DOMAIN not in sb.get_current_url():
            return

        try:
            sb.wait_for_element("#loginInner_u", timeout=15)
            sb.type("#loginInner_u", self._config.rakuten_username)
            sb.type("#loginInner_p", self._config.rakuten_password)
            time.sleep(0.5)
            sb.click("input.loginButton")
        except Exception as e:
            raise LoginError(f"ログインフォームの操作に失敗しました: {e}") from e

        # /logini, /ppstep1 などの中間ステップを処理（session.py と同じロジック）
        _clicked_pages: set[str] = set()
        for i in range(120):
            time.sleep(0.3)
            current_url = sb.get_current_url()
            if LOGIN_DOMAIN not in current_url:
                return
            source = sb.get_page_source()
            if "ログインIDまたはパスワードが" in source or "入力内容に誤り" in source:
                raise LoginError("IDまたはパスワードが正しくありません")
            if current_url not in _clicked_pages:
                try:
                    sb.wait_for_element('input[value="次へ"]', timeout=1)
                    sb.click('input[value="次へ"]')
                    _clicked_pages.add(current_url)
                    logger.info("「次へ」クリック (URL: %s)", current_url)
                    time.sleep(1)
                    continue
                except Exception:
                    pass
        raise LoginError("ログイン後のリダイレクトがタイムアウトしました")

    def _go_to_cart(self, sb, item: SearchItem) -> None:
        logger.info("[%s] カートページへ遷移します", item.id)
        sb.open(CART_URL)

        # React SPA のため JS 読み込みを待つ（「購入手続き」ボタンを最長10秒待機）
        try:
            sb.wait_for_element('button[aria-label="購入手続き"]', timeout=10)
            logger.info("[%s] カート内の商品を確認（購入手続きボタン検出）", item.id)
            return
        except Exception:
            pass

        # 空カートメッセージがあれば品切れ確定
        source = sb.get_page_source()
        if "カートに商品がありません" in source or "カートは空" in source:
            raise CheckoutError(
                f"[{item.id}] カートに商品が見つかりません。"
                "在庫確認からカートページ遷移の間に売り切れた可能性があります。"
            )

        # 購入手続きボタンが見つからないが空カートでもない場合はページ構造が変わった可能性
        logger.warning("[%s] カートページURL: %s", item.id, sb.get_current_url())
        logger.warning("[%s] カートページHTML (先頭2000文字):\n%s", item.id, source[:2000])
        raise CheckoutError(
            f"[{item.id}] 購入手続きボタンが見つかりません（カートページ構造が変わった可能性）"
        )

    def _proceed_to_checkout(self, sb, item: SearchItem) -> None:
        logger.info("[%s] 注文手続きへ進みます", item.id)

        clicked = False
        for selector in _PROCEED_SELECTORS:
            if sb.is_element_present(selector):
                sb.click(selector)
                clicked = True
                break
        if not clicked:
            raise CheckoutError(f"[{item.id}] 注文手続きボタンが見つかりません")

        # SSO へのリダイレクト確認（最大 4 秒・0.2 秒間隔でポーリング）
        _cart_host = "cart.step.rakuten.co.jp"
        for _ in range(20):
            time.sleep(0.2)
            url = self._cdp_url(sb)
            if SSO_DOMAIN in url:
                break               # SSO 検出 → 以降の SSO 処理へ
            if _cart_host not in url:
                return              # カートホスト外かつ非 SSO → 通常チェックアウト
        else:
            return                  # 4 秒経過・SSO なし → そのまま通過

        # SSO ページ検出: r10-challenger の PoW がバックグラウンドで走るため
        # 入力欄が DOM に現れるまでポーリングし、出現次第操作する（最大 20 秒）
        logger.info("[%s] SSO 再認証ページ検出", item.id)

        # Step1: メール入力ページ（入力欄の出現を最大 20 秒待機）
        logger.info("[%s] SSO: メール入力欄の準備を待機（最大 20 秒）", item.id)
        for _ in range(40):
            time.sleep(0.5)
            if self._cdp_element_center(sb, "#user_id"):
                break

        email_pos = self._cdp_element_center(sb, "#user_id")
        if not email_pos:
            raise CheckoutError(f"[{item.id}] SSO: メール入力欄が見つかりません")
        self._cdp_mouse_click(sb, email_pos["x"], email_pos["y"])
        time.sleep(0.3)
        sb.driver.execute_cdp_cmd("Input.insertText", {"text": self._config.rakuten_username})
        time.sleep(0.3)

        btn1 = self._cdp_visible_button(sb, "次へ")
        if not btn1:
            raise CheckoutError(f"[{item.id}] SSO: メールページの次へボタンが見つかりません")
        self._cdp_mouse_click(sb, btn1["x"], btn1["y"])
        logger.info("[%s] SSO: メール入力・次へクリック完了", item.id)

        # パスワードページへの遷移待ち（0.2 秒間隔）
        for _ in range(75):
            time.sleep(0.2)
            if "password" in self._cdp_url(sb):
                break

        # Step2: パスワード入力ページ（入力欄の出現を最大 20 秒待機）
        logger.info("[%s] SSO: パスワード入力欄の準備を待機（最大 20 秒）", item.id)
        for _ in range(40):
            time.sleep(0.5)
            if self._cdp_element_center(sb, "#password_current"):
                break

        pw_pos = self._cdp_element_center(sb, "#password_current")
        if not pw_pos:
            raise CheckoutError(f"[{item.id}] SSO: パスワード入力欄が見つかりません")
        self._cdp_mouse_click(sb, pw_pos["x"], pw_pos["y"])
        time.sleep(0.3)
        sb.driver.execute_cdp_cmd("Input.insertText", {"text": self._config.rakuten_password})
        time.sleep(0.3)

        btn2 = self._cdp_visible_button(sb, "次へ")
        if not btn2:
            raise CheckoutError(f"[{item.id}] SSO: パスワードページの次へボタンが見つかりません")
        self._cdp_mouse_click(sb, btn2["x"], btn2["y"])
        logger.info("[%s] SSO: パスワード入力・次へクリック完了", item.id)

        # SSO 完了後のリダイレクト待機（最大 60 秒・0.5 秒間隔）
        for i in range(120):
            time.sleep(0.5)
            url = self._cdp_url(sb)
            if SSO_DOMAIN not in url:
                logger.info("[%s] SSO 完了 (URL: %s)", item.id, url)
                return

        raise CheckoutError(f"[{item.id}] SSO 再認証後のリダイレクトがタイムアウトしました")

    def _cdp_url(self, sb) -> str:
        try:
            r = sb.driver.execute_cdp_cmd(
                "Runtime.evaluate", {"expression": "location.href", "returnByValue": True}
            )
            return r.get("result", {}).get("value", "")
        except Exception:
            return ""

    def _cdp_mouse_click(self, sb, x: float, y: float) -> None:
        for etype in ("mouseMoved", "mousePressed", "mouseReleased"):
            sb.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                "type": etype, "x": x, "y": y,
                "button": "left", "clickCount": 1 if etype != "mouseMoved" else 0,
            })
            time.sleep(0.05)

    def _cdp_element_center(self, sb, selector: str) -> dict | None:
        r = sb.driver.execute_cdp_cmd("Runtime.evaluate", {
            "expression": (
                f"(function(){{"
                f"var e=document.querySelector('{selector}');"
                f"if(!e)return null;"
                f"var rect=e.getBoundingClientRect();"
                f"return {{x:rect.left+rect.width/2,y:rect.top+rect.height/2}};"
                f"}})();"
            ),
            "returnByValue": True,
        })
        return r.get("result", {}).get("value")

    def _cdp_visible_button(self, sb, label: str) -> dict | None:
        """表示中（幅・高さ > 0）の div[role=button] または button 要素でテキスト一致を返す"""
        l = label.replace("'", "\\'")
        r = sb.driver.execute_cdp_cmd("Runtime.evaluate", {
            "expression": (
                f"(function(){{"
                f"var els=document.querySelectorAll('div[role=\"button\"]');"
                f"for(var i=0;i<els.length;i++){{"
                f"if(els[i].textContent.trim()==='{l}'){{"
                f"var rect=els[i].getBoundingClientRect();"
                f"if(rect.width>0&&rect.height>0)return{{x:rect.left+rect.width/2,y:rect.top+rect.height/2}};"
                f"}}}}"
                f"var btns=document.querySelectorAll('button');"
                f"for(var j=0;j<btns.length;j++){{"
                f"if(btns[j].textContent.trim()==='{l}'){{"
                f"var rect2=btns[j].getBoundingClientRect();"
                f"if(rect2.width>0&&rect2.height>0)return{{x:rect2.left+rect2.width/2,y:rect2.top+rect2.height/2}};"
                f"}}}}"
                f"return null;}})();"
            ),
            "returnByValue": True,
        })
        return r.get("result", {}).get("value")

    def _handle_delivery_modal(self, sb, item: SearchItem) -> None:
        """お届け日時指定モーダルが出ていれば「最短お届け日」を選択して決定する"""
        # モーダルの有無を確認（最大 15 秒待機）
        modal_sel = 'input[value="earliestDate"]'
        found = False
        for _ in range(50):
            time.sleep(0.3)
            r = sb.driver.execute_cdp_cmd("Runtime.evaluate", {
                "expression": f"!!document.querySelector('{modal_sel}')",
                "returnByValue": True,
            })
            if r.get("result", {}).get("value"):
                found = True
                break

        if not found:
            return  # モーダルなし

        logger.info("[%s] お届け日時モーダルを検出。最短お届け日を選択します", item.id)

        # 「最短お届け日」テキストの span を直接クリックする。
        # pointer クラスの祖先は幅全体に広がるため中央が右端シェブロン付近にずれる。
        _js_find_row = """
(function() {
    var spans = document.querySelectorAll('span');
    for (var i = 0; i < spans.length; i++) {
        if (spans[i].textContent.trim() === '最短お届け日') {
            var rect = spans[i].getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0)
                return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
        }
    }
    var radio = document.querySelector('input[value="earliestDate"]');
    if (radio) {
        var r2 = radio.getBoundingClientRect();
        return { x: r2.left + r2.width / 2, y: r2.top + r2.height / 2 };
    }
    return null;
})()
"""
        r = sb.driver.execute_cdp_cmd("Runtime.evaluate", {
            "expression": _js_find_row,
            "returnByValue": True,
        })
        row_pos = r.get("result", {}).get("value")
        if not row_pos:
            logger.warning("[%s] 最短お届け日の行が見つかりません", item.id)
            return

        self._cdp_mouse_click(sb, row_pos["x"], row_pos["y"])
        logger.info("[%s] 最短お届け日の行をクリックしました", item.id)
        time.sleep(0.3)

        # 「決定する」ボタンが有効化されるまで待機（最大 5 秒・0.2 秒間隔）
        for _ in range(25):
            time.sleep(0.2)
            r = sb.driver.execute_cdp_cmd("Runtime.evaluate", {
                "expression": "!document.querySelector('button[aria-label=\"決定する\"]')?.disabled",
                "returnByValue": True,
            })
            if r.get("result", {}).get("value"):
                logger.info("[%s] 決定するボタンが有効化されました", item.id)
                break

        # 「決定する」は通常の <button> 要素
        decide_pos = self._cdp_element_center(sb, 'button[aria-label="決定する"]:not([disabled])')
        if decide_pos:
            self._cdp_mouse_click(sb, decide_pos["x"], decide_pos["y"])
            logger.info("[%s] お届け日時モーダル: 決定するクリック完了", item.id)
            time.sleep(0.5)
        else:
            logger.warning("[%s] 決定するボタンが有効化されませんでした（ラジオ選択失敗の可能性）", item.id)

    def _confirm_order(self, sb, item: SearchItem) -> str | None:
        logger.info("[%s] 注文を確定します", item.id)
        # React SPA のため CDP 実座標クリックを優先、WebDriver をフォールバックとする
        btn = self._cdp_visible_button(sb, "注文を確定する")
        if btn:
            self._cdp_mouse_click(sb, btn["x"], btn["y"])
        else:
            clicked = False
            for selector in _CONFIRM_SELECTORS:
                if sb.is_element_present(selector):
                    sb.click(selector)
                    clicked = True
                    break
            if not clicked:
                raise CheckoutError(f"[{item.id}] 注文確定ボタンが見つかりません")

        # 注文完了ページを待つ（最大 60 秒・1 秒間隔）
        for _ in range(60):
            time.sleep(1)
            try:
                r = sb.driver.execute_cdp_cmd(
                    "Runtime.evaluate",
                    {"expression": "document.body.innerText", "returnByValue": True},
                )
                text = r.get("result", {}).get("value", "")
            except Exception:
                text = sb.get_page_source()
            if "ご注文ありがとう" in text or "注文番号" in text:
                match = _ORDER_NUMBER_PATTERN.search(text)
                return match.group(1) if match else None

        raise CheckoutError(f"[{item.id}] 注文完了ページへの遷移がタイムアウトしました")
