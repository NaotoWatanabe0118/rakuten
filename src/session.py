import json
import logging
import time
from pathlib import Path

import requests
from seleniumbase import SB

from src.config import Config
from src.exceptions import LoginError

logger = logging.getLogger(__name__)

LOGIN_URL = "https://grp01.id.rakuten.co.jp/rms/nid/login"
LOGIN_DOMAIN = "grp01.id.rakuten.co.jp"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.rakuten.co.jp/",
}


def build_session(cookies: list[dict]) -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    return session


def load_cookies(cookies_file: Path) -> list[dict] | None:
    if not cookies_file.exists():
        return None
    try:
        return json.loads(cookies_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_cookies(cookies: list[dict], cookies_file: Path) -> None:
    cookies_file.parent.mkdir(parents=True, exist_ok=True)
    cookies_file.write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def login(config: Config) -> list[dict]:
    logger.info("Seleniumでログインを開始します")
    with SB(
        uc=True,
        headless=config.headless,
        user_data_dir=config.chrome_user_data_dir,
        chromium_arg="--no-sandbox --disable-dev-shm-usage",
    ) as sb:
        sb.driver.set_page_load_timeout(config.selenium_page_timeout)
        sb.open(LOGIN_URL)
        time.sleep(2)

        # すでにログイン済みの場合はそのままCookieを返す
        if LOGIN_DOMAIN not in sb.get_current_url():
            logger.info("セッションが有効です（再ログイン不要）")
            return sb.driver.get_cookies()

        # ログインフォームへの入力
        try:
            sb.wait_for_element("#loginInner_u", timeout=15)
            sb.type("#loginInner_u", config.rakuten_username)
            sb.type("#loginInner_p", config.rakuten_password)
            time.sleep(0.5)
            sb.click("input.loginButton")
        except Exception as e:
            raise LoginError(f"ログインフォームの操作に失敗しました: {e}") from e

        # ログイン後の中間ステップを処理しながらリダイレクトを待つ（最大300秒）
        logger.info("ログイン処理中...")
        _clicked_pages: set[str] = set()

        for i in range(300):
            time.sleep(1)
            current_url = sb.get_current_url()

            # ログインドメインを離れたら完了
            if LOGIN_DOMAIN not in current_url:
                break

            source = sb.get_page_source()

            # 認証情報エラー検出
            if "ログインIDまたはパスワードが" in source or "入力内容に誤り" in source:
                raise LoginError("IDまたはパスワードが正しくありません。.env を確認してください")

            # 「次へ」ボタンが存在するページは自動クリック
            # 対象: /logini, /ppstep1 など楽天のログイン中間ステップ
            if current_url not in _clicked_pages:
                try:
                    sb.wait_for_element('input[value="次へ"]', timeout=2)
                    sb.click('input[value="次へ"]')
                    _clicked_pages.add(current_url)
                    logger.info("「次へ」ボタンをクリックしました (URL: %s)", current_url)
                    time.sleep(3)
                    continue
                except Exception:
                    pass

            # 認証コード入力が必要な場合の案内（1回だけ表示）
            if i == 10:
                print("\n" + "=" * 60)
                print("【操作が必要な場合】ブラウザに認証コード入力画面が表示されていれば")
                print("  コードを入力して「次へ」を押してください。自動で続行します。")
                print("=" * 60 + "\n")

            if i % 30 == 29:
                logger.info("ログイン待機中... %d秒経過 (URL: %s)", i + 1, current_url)
        else:
            raise LoginError(
                f"ログイン後のリダイレクトが300秒でタイムアウトしました。"
                f"現在のURL: {sb.get_current_url()}"
            )

        logger.info("ログイン成功 (URL: %s)", sb.get_current_url())
        # WebDriver接続が切れている場合もCDP経由でCookieを取得する
        try:
            result = sb.driver.execute_cdp_cmd("Network.getAllCookies", {})
            cookies = result.get("cookies", [])
            logger.info("Cookie取得成功 (%d件, CDP経由)", len(cookies))
            return cookies
        except Exception as cdp_err:
            logger.debug("CDP Cookie取得失敗: %s, WebDriver経由を試みます", cdp_err)
        # フォールバック: WebDriver経由
        for attempt in range(5):
            try:
                cookies = sb.driver.get_cookies()
                logger.info("Cookie取得成功 (%d件, WebDriver経由)", len(cookies))
                return cookies
            except Exception as e:
                if attempt < 4:
                    time.sleep(1)
                else:
                    raise LoginError(f"Cookie取得に失敗しました: {e}") from e
        return []  # unreachable


def get_session(config: Config) -> requests.Session:
    cookies = load_cookies(config.cookies_file)
    if cookies:
        logger.info("保存済みCookieを読み込みます")
        return build_session(cookies)

    cookies = login(config)
    save_cookies(cookies, config.cookies_file)
    return build_session(cookies)


def refresh_session(config: Config) -> requests.Session:
    logger.info("セッションを更新します（再ログイン）")
    config.cookies_file.unlink(missing_ok=True)
    cookies = login(config)
    save_cookies(cookies, config.cookies_file)
    return build_session(cookies)
