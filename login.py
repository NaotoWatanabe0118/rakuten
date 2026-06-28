"""
初回セットアップ用ログインスクリプト。
VPS上で data/cookies.json が存在しない場合に実行する。

使い方:
  python login.py

Xvfb が起動済みの環境（docker-entrypoint.sh 経由）または
HEADLESS=0 でローカル実行する場合に使用する。
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from src.config import load_config
from src.exceptions import LoginError
from src.session import login, save_cookies

config = load_config()

logging.info("楽天にログインして Cookie を保存します...")
logging.info("ユーザー: %s", config.rakuten_username)

try:
    cookies = login(config)
    save_cookies(cookies, config.cookies_file)
    logging.info("Cookie を保存しました: %s (%d件)", config.cookies_file, len(cookies))
    logging.info("これでボットを起動できます: python -m src.main")
except LoginError as e:
    logging.error("ログイン失敗: %s", e)
    sys.exit(1)
