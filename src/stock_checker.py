import json as _json
import logging
from datetime import datetime

import requests

from src.exceptions import StockCheckError
from src.models import CartAddResult, SearchItem

logger = logging.getLogger(__name__)

CART_ADD_URL = "https://ts.direct.step.rakuten.co.jp/rms/mall/cartAdd/"

# カート追加成功のresultCode
_SUCCESS_CODE = "0"

# セッション切れを示すresultCode（再ログインが必要）
_SESSION_EXPIRED_CODES = {"R0401", "R0403"}


def check_stock(session: requests.Session, item: SearchItem) -> CartAddResult:
    params = {
        "itemid": item.itemid,
        "shopid": item.shopid,
        "units": str(item.units),
    }
    try:
        resp = session.get(CART_ADD_URL, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise StockCheckError(f"[{item.id}] APIリクエスト失敗: {e}") from e

    try:
        data = resp.json()
    except ValueError as e:
        raise StockCheckError(f"[{item.id}] レスポンスのJSONパース失敗: {e}") from e

    result_code = data.get("resultCode", "")
    result_message = data.get("resultMessage", "")
    success = result_code == _SUCCESS_CODE

    if success:
        logger.info("[%s] 在庫あり（カート追加成功）: %s", item.id, item.name)
    else:
        logger.debug("[%s] 在庫なし: %s (resultCode=%s)", item.id, item.name, result_code)

    return CartAddResult(
        item_id=item.id,
        success=success,
        result_code=result_code,
        result_message=result_message,
        checked_at=datetime.utcnow(),
    )


def is_session_expired(result: CartAddResult) -> bool:
    return result.result_code in _SESSION_EXPIRED_CODES


def check_stock_with_browser(sb, item: SearchItem) -> CartAddResult:
    """常駐ブラウザ経由で在庫チェック（VPS IP ブロック回避・カート追加を兼ねる）。

    resultCode=0 の場合、カートへの追加も同時に完了しているため
    そのまま購入フローへ進める。
    """
    url = (
        f"{CART_ADD_URL}"
        f"?itemid={item.itemid}&shopid={item.shopid}&units={item.units}"
    )
    try:
        sb.open(url)
        current_url = sb.get_current_url()
        logger.info("[%s] cartAdd 後 URL: %s", item.id, current_url)

        # Chrome JSON ビューア(<pre>)・body.innerText・documentElement の順で試行
        r = sb.driver.execute_cdp_cmd("Runtime.evaluate", {
            "expression": """
            (function() {
                var pre = document.querySelector('pre');
                if (pre && pre.textContent.trim()) return pre.textContent.trim();
                var body = document.body.innerText.trim();
                if (body) return body;
                return document.documentElement.innerText.trim();
            })()
            """,
            "returnByValue": True,
        })
        text = (r.get("result", {}).get("value") or "").strip()

        # ページソースも取得して診断ログに出力
        source = sb.get_page_source()
        logger.info("[%s] page_source 先頭400文字: %s", item.id, source[:400])

        if not text:
            raise StockCheckError(f"[{item.id}] 空のレスポンス（URL: {current_url}）")
        data = _json.loads(text)
    except StockCheckError:
        raise
    except Exception as e:
        raise StockCheckError(f"[{item.id}] ブラウザ経由の在庫チェック失敗: {e}") from e

    result_code = data.get("resultCode", "")
    result_message = data.get("resultMessage", "")
    success = result_code == _SUCCESS_CODE

    if success:
        logger.info("[%s] 在庫あり（カート追加成功）: %s", item.id, item.name)
    else:
        logger.debug("[%s] 在庫なし: %s (resultCode=%s)", item.id, item.name, result_code)

    return CartAddResult(
        item_id=item.id,
        success=success,
        result_code=result_code,
        result_message=result_message,
        checked_at=datetime.utcnow(),
    )
