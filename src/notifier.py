import logging
import time

import requests

from src.models import CartAddResult, PurchaseResult, SearchItem

logger = logging.getLogger(__name__)

_COLOR_GREEN = 0x00FF00
_COLOR_BLUE = 0x0000FF
_COLOR_ORANGE = 0xFF8C00
_COLOR_RED = 0xFF0000
_COLOR_DARK_RED = 0x8B0000


class Notifier:
    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url
        self._session = requests.Session()

    def _send(self, embed: dict) -> None:
        payload = {"embeds": [embed]}
        for attempt in range(3):
            try:
                resp = self._session.post(
                    self._webhook_url, json=payload, timeout=10
                )
                if resp.status_code == 429:
                    retry_after = resp.json().get("retry_after", 1)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return
            except requests.RequestException as e:
                logger.warning("Discord通知の送信に失敗しました (attempt %d): %s", attempt + 1, e)
                time.sleep(2 ** attempt)

    def _embed(
        self,
        title: str,
        description: str,
        color: int,
        fields: list[dict] | None = None,
    ) -> dict:
        embed: dict = {"title": title, "description": description, "color": color}
        if fields:
            embed["fields"] = fields
        return embed

    def notify_stock_detected(self, item: SearchItem, result: CartAddResult) -> None:
        self._send(
            self._embed(
                title=f"在庫あり検出: {item.name}",
                description="カートへの追加に成功しました。購入フローを開始します。",
                color=_COLOR_GREEN,
                fields=[
                    {"name": "商品ID", "value": item.itemid, "inline": True},
                    {"name": "ショップID", "value": item.shopid, "inline": True},
                ],
            )
        )

    def notify_purchase_complete(
        self, item: SearchItem, result: PurchaseResult
    ) -> None:
        fields = [{"name": "商品", "value": item.name, "inline": False}]
        if result.order_number:
            fields.append(
                {"name": "注文番号", "value": result.order_number, "inline": False}
            )
        self._send(
            self._embed(
                title=f"購入完了: {item.name}",
                description="注文が確定しました。",
                color=_COLOR_BLUE,
                fields=fields,
            )
        )

    def notify_purchase_failed(self, item: SearchItem, reason: str) -> None:
        self._send(
            self._embed(
                title=f"購入失敗: {item.name}",
                description=f"エラー: {reason}",
                color=_COLOR_RED,
            )
        )

    def notify_step_error(self, item: SearchItem, step: str, error: Exception) -> None:
        self._send(
            self._embed(
                title=f"購入フローエラー: {item.name}",
                description=f"ステップ **{step}** でエラーが発生しました。",
                color=_COLOR_ORANGE,
                fields=[
                    {"name": "エラー内容", "value": str(error)[:1024], "inline": False},
                ],
            )
        )

    def notify_error(self, item: SearchItem, error: Exception) -> None:
        self._send(
            self._embed(
                title=f"監視エラー: {item.name}",
                description=str(error),
                color=_COLOR_DARK_RED,
            )
        )
