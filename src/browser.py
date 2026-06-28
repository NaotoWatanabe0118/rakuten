import logging
from pathlib import Path

from seleniumbase import SB

from src.config import Config

logger = logging.getLogger(__name__)

# Chrome がクラッシュ時に残すロックファイル
_CHROME_LOCK_FILES = [
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
]


class BrowserSession:
    """購入フロー用の常駐ブラウザセッション。

    在庫検知前にブラウザを起動・ログインしておくことで、
    在庫検知後のブラウザ起動コスト（約14秒）をゼロにする。
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._sb_ctx: SB | None = None
        self._sb = None

    def _clean_chrome_locks(self) -> None:
        """前回クラッシュで残ったChromeのシングルトンロックを削除する。"""
        profile_dir = Path(self._config.chrome_user_data_dir)
        if not profile_dir.exists():
            return
        for name in _CHROME_LOCK_FILES:
            lock = profile_dir / name
            if lock.exists():
                try:
                    lock.unlink()
                    logger.info("Chrome ロックファイルを削除: %s", lock)
                except Exception as e:
                    logger.warning("Chrome ロックファイル削除失敗: %s: %s", lock, e)

    def start(self) -> None:
        self._clean_chrome_locks()
        self._sb_ctx = SB(
            uc=True,
            headless=self._config.headless,
            user_data_dir=self._config.chrome_user_data_dir,
            chromium_arg="--no-sandbox --disable-dev-shm-usage --disable-gpu",
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
