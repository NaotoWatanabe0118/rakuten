import logging

from seleniumbase import SB

from src.config import Config

logger = logging.getLogger(__name__)


class BrowserSession:
    """購入フロー用の常駐ブラウザセッション。

    在庫検知前にブラウザを起動・ログインしておくことで、
    在庫検知後のブラウザ起動コスト（約14秒）をゼロにする。
    user_data_dir は使用しない（クラッシュ後のプロファイル破損を防ぐため）。
    Cookieは _restore_cookies で別途設定する。
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._sb_ctx: SB | None = None
        self._sb = None

    def start(self) -> None:
        self._sb_ctx = SB(
            uc=True,
            headless=self._config.headless,
            chromium_arg=(
                "--no-sandbox "
                "--disable-dev-shm-usage "
                "--disable-gpu "
                "--disable-software-rasterizer "
                "--disable-extensions "
                "--no-first-run "
                "--disable-default-apps"
            ),
        )
        self._sb = self._sb_ctx.__enter__()
        self._sb.driver.set_page_load_timeout(self._config.selenium_page_timeout)
        logger.info("ブラウザを起動しました")

    def stop(self) -> None:
        if self._sb_ctx:
            try:
                self._sb_ctx.__exit__(None, None, None)
            except Exception:
                pass
            self._sb_ctx = None
            self._sb = None
        logger.info("ブラウザを終了しました")

    @property
    def sb(self):
        return self._sb

    def is_alive(self) -> bool:
        """CDP コマンドで応答を確認"""
        try:
            self._sb.driver.execute_cdp_cmd(
                "Runtime.evaluate", {"expression": "1", "returnByValue": True}
            )
            return True
        except Exception:
            return False
