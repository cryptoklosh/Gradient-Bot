import os
import time
import logging
import random
import requests
import zipfile
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv
from pathlib import Path
from fake_useragent import UserAgent
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import shutil
import colorama
from colorama import Fore, Style

# Инициализация colorama
colorama.init(autoreset=True)

# Глобальные переменные для отслеживания статуса
account_statuses = {}
status_lock = threading.Lock()
proxies_lock = threading.Lock()
chromedriver_lock = threading.Lock()

# Конфигурация параметров
CONFIG = {
    "PAGE_LOAD_TIMEOUT": 30,
    "SCRIPT_TIMEOUT": 30,
    "ELEMENT_WAIT_TIMEOUT": 30,
    "EXTENSION_WAIT": 30,
    "STATUS_CHECK_INTERVAL_MIN": 1800,
    "STATUS_CHECK_INTERVAL_MAX": 5400,
    "TASK_INTERVAL_MIN": 20,
    "TASK_INTERVAL_MAX": 40,
    "RETRY_INTERVAL": 5,
    "PROXY_RELOAD_INTERVAL": 60,
    "MAX_THREADS": 17,
    "PROXY_THRESHOLD": 10,
    "STATUS_CHECK_INTERVAL": 3600,
    "PROXY_REFRESH_INTERVAL": 300
}

# Класс для хранения статуса аккаунта
class AccountStatus:
    def __init__(self):
        self.status = "initializing"
        self.last_update = time.time()
        self.message = ""
        self.proxy = ""
        self.node_id = 0

# Логирование
class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        message = super().format(record)
        return color + message + Style.RESET_ALL

HEADLESS = True

banner = r"""
 _   _           _  _____
| \ | |         | ||____ |
|  \| | ___   __| |    / /_ __
| .  |/ _ \ / _ |    \ \ '__|
| |\  | (_) | (_| |.___/ / |
\_| \_/\___/ \__,_|\____/|_|

Менеджер Gradient Bot
    @nod3r - Мультиаккаунт версия
"""
print(banner)
time.sleep(1)

load_dotenv()

logger = logging.getLogger()
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = ColoredFormatter("%(asctime)s - %(message)s", "%H:%M:%S")
console_handler.setFormatter(formatter)
logger.handlers = [console_handler]

EXTENSION_ID = "caacbgbklghmpodbdafajbgdnegacfmo"
CRX_URL = ("https://clients2.google.com/service/update2/crx?"
           "response=redirect&prodversion=98.0.4758.102&acceptformat=crx2,crx3&"
           "x=id%3D{0}%26uc&nacl_arch=x86-64".format(EXTENSION_ID))
EXTENSION_FILENAME = "app.crx"

ua = UserAgent()

def update_account_status(email, status, message="", proxy="", node_id=0):
    """Обновление статуса аккаунта"""
    with status_lock:
        if email not in account_statuses:
            account_statuses[email] = AccountStatus()
        account_statuses[email].status = status
        account_statuses[email].message = message
        account_statuses[email].proxy = proxy
        account_statuses[email].node_id = node_id
        account_statuses[email].last_update = time.time()

def status_monitor(accounts):
    """Фоновая проверка статуса аккаунтов"""
    while True:
        time.sleep(CONFIG["STATUS_CHECK_INTERVAL"])
        with status_lock:
            working = 0
            failed = []
            for email, _ in accounts:
                status = account_statuses.get(email, AccountStatus())
                if status.status == "working":
                    working += 1
                else:
                    failed.append(f"{email} (Node {status.node_id}: {status.message})")

            total = len(accounts)
            logger.info(f"\n{Fore.CYAN}=== СТАТИСТИКА АККАУНТОВ ==={Style.RESET_ALL}")
            logger.info(f"{Fore.GREEN}Работают: {working}/{total}{Style.RESET_ALL}")
            if failed:
                logger.info(f"{Fore.RED}Проблемные:{Style.RESET_ALL}")
                for acc in failed:
                    logger.info(f"  {acc}")

def load_accounts():
    accounts = []
    if os.path.exists("accounts.txt"):
        with open("accounts.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        email = parts[0].strip()
                        password = ":".join(parts[1:]).strip()
                        accounts.append((email, password))
        if accounts:
            logger.info(f"Загружено {len(accounts)} аккаунтов из accounts.txt.")
    else:
        user = os.getenv("APP_USER")
        password = os.getenv("APP_PASS")
        if user and password:
            accounts.append((user, password))
            logger.info("Используется один аккаунт из переменных окружения.")
        else:
            logger.error("Аккаунты не заданы. Создайте файл accounts.txt или задайте APP_USER и APP_PASS в .env.")
            exit(1)
    return accounts

def load_proxies():
    proxies_list = []
    if os.path.exists("active_proxies.txt"):
        with open("active_proxies.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    proxies_list.append(line)
        if proxies_list:
            logger.info(f"Загружено {len(proxies_list)} прокси из active_proxies.txt.")
    else:
        logger.warning("Прокси не найдены в active_proxies.txt. Работаем в режиме прямого соединения.")
        proxies_list = [None]
    return proxies_list

def create_proxy_auth_extension(host, port, username, password, scheme='http', plugin_path='proxy_auth_plugin.zip'):
    manifest_json = """
{
  "version": "1.0.0",
  "manifest_version": 2,
  "name": "Chrome Proxy Auth Extension",
  "permissions": [
    "proxy",
    "tabs",
    "unlimitedStorage",
    "storage",
    "<all_urls>",
    "webRequest",
    "webRequestBlocking"
  ],
  "background": {
    "scripts": ["background.js"]
  },
  "minimum_chrome_version": "22.0.0"
}
"""
    background_js = f"""
var config = {{
    mode: "fixed_servers",
    rules: {{
        singleProxy: {{
            scheme: "{scheme}",
            host: "{host}",
            port: parseInt({port})
        }},
        bypassList: ["localhost"]
    }}
}};
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function(){{}});
function callbackFn(details) {{
    return {{
        authCredentials: {{
            username: "{username}",
            password: "{password}"
        }}
    }};
}}
chrome.webRequest.onAuthRequired.addListener(
    callbackFn,
    {{urls: ["<all_urls>"]}},
    ["blocking"]
);
"""
    with zipfile.ZipFile(plugin_path, 'w') as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)
    return plugin_path

def download_extension():
    logger.info(f"Скачивание расширения с: {CRX_URL}")
    ext_path = Path(EXTENSION_FILENAME)
    if ext_path.exists() and time.time() - ext_path.stat().st_mtime < 86400:
        logger.info("Расширение уже скачано, пропускаем скачивание...")
        return
    try:
        response = requests.get(CRX_URL, headers={"User-Agent": ua.random}, timeout=10)
        if response.status_code == 200:
            ext_path.write_bytes(response.content)
            logger.info("Расширение успешно скачано")
        else:
            logger.error(f"Не удалось скачать расширение: {response.status_code}")
            exit(1)
    except Exception as e:
        logger.error(f"Ошибка при скачивании расширения: {e}")
        exit(1)

def setup_chrome_options(proxy=None):
    chrome_options = Options()
    if HEADLESS:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument(f"user-agent={ua.random}")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-web-security")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--webrtc-ip-handling-policy=disable_non_proxied_udp")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")
    chrome_options.add_argument("--disable-renderer-backgrounding")

    if proxy:
        if "@" in proxy:
            parsed = urlparse(proxy)
            plugin_path = create_proxy_auth_extension(
                parsed.hostname, parsed.port, parsed.username, parsed.password, parsed.scheme
            )
            chrome_options.add_extension(plugin_path)
        else:
            chrome_options.add_argument("--proxy-server=" + proxy)
    else:
        logger.info("Режим прямого соединения (без прокси).")

    ext_path = Path(EXTENSION_FILENAME).resolve()
    if ext_path.exists():
        chrome_options.add_extension(str(ext_path))
    else:
        logger.warning("Расширение для приложения не найдено.")

    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    return chrome_options

def login_to_app(driver, account):
    email, password = account
    try:
        driver.get("https://app.gradient.network/")
        WebDriverWait(driver, CONFIG["ELEMENT_WAIT_TIMEOUT"]).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[placeholder="Enter Email"]'))
        )
        driver.find_element(By.CSS_SELECTOR, '[placeholder="Enter Email"]').send_keys(email)
        driver.find_element(By.CSS_SELECTOR, '[type="password"]').send_keys(password)
        driver.find_element(By.CSS_SELECTOR, "button").click()
        WebDriverWait(driver, CONFIG["ELEMENT_WAIT_TIMEOUT"]).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href="/dashboard/setting"]'))
        )
        logger.info(f"Успешная авторизация для аккаунта: {email}")
        return True
    except Exception as e:
        logger.error(f"Ошибка авторизации для {email}: {str(e)}")
        return False

def check_gradient_status(driver):
    try:
        driver.get(f"chrome-extension://{EXTENSION_ID}/popup.html")
        status_element = WebDriverWait(driver, 200).until(
            EC.presence_of_element_located((By.XPATH, '//div[contains(text(), "Status")]'))
        )
        return status_element.text
    except Exception as e:
        logger.warning(f"Ошибка проверки статуса расширения: {str(e)}")
        raise

def get_chromedriver_path():
    with chromedriver_lock:
        try:
            return ChromeDriverManager().install()
        except Exception as e:
            logger.error(f"Ошибка ChromeDriver: {str(e)}")
            cache_dir = os.path.join(os.path.expanduser("~"), ".wdm")
            shutil.rmtree(cache_dir, ignore_errors=True)
            return ChromeDriverManager().install()

def test_proxy_speed(proxy, test_url="https://www.google.com", timeout=5):
    start = time.time()
    try:
        response = requests.get(test_url, proxies={"http": proxy, "https": proxy}, timeout=timeout)
        return time.time() - start
    except:
        return None

def attempt_connection(proxy, account):
    email, password = account
    try:
        chrome_options = setup_chrome_options(proxy)
        driver_path = get_chromedriver_path()
        driver = webdriver.Chrome(service=Service(driver_path), options=chrome_options)
        driver.set_page_load_timeout(CONFIG["PAGE_LOAD_TIMEOUT"])
        driver.set_script_timeout(CONFIG["SCRIPT_TIMEOUT"])
        if not login_to_app(driver, account):
            driver.quit()
            return None
        return driver
    except Exception as e:
        logger.error(f"Ошибка подключения для {email}: {str(e)}")
        try: driver.quit()
        except: pass
        return None

def worker(account, node_id):
    email, password = account
    proxies = load_proxies()
    last_proxy_refresh = 0

    while True:
        try:
            if time.time() - last_proxy_refresh > CONFIG["PROXY_REFRESH_INTERVAL"]:
                proxies = load_proxies()
                last_proxy_refresh = time.time()

            proxy = random.choice(proxies) if proxies else None
            update_account_status(email, "connecting", f"Попытка подключения", proxy, node_id)

            driver = attempt_connection(proxy, account)
            if not driver:
                update_account_status(email, "error", "Ошибка подключения", proxy, node_id)
                time.sleep(CONFIG["RETRY_INTERVAL"])
                continue

            update_account_status(email, "working", "Успешное подключение", proxy, node_id)

            while True:
                try:
                    status = check_gradient_status(driver)
                    if "Good" not in status:
                        raise Exception(f"Плохой статус: {status}")
                except Exception as e:
                    update_account_status(email, "error", str(e), proxy, node_id)
                    break

                try:
                    driver.refresh()
                    time.sleep(random.uniform(CONFIG["TASK_INTERVAL_MIN"], CONFIG["TASK_INTERVAL_MAX"]))
                except Exception as e:
                    update_account_status(email, "error", str(e), proxy, node_id)
                    break

        except Exception as e:
            update_account_status(email, "error", str(e), proxy, node_id)
            time.sleep(CONFIG["RETRY_INTERVAL"])

        finally:
            try: driver.quit()
            except: pass

def main():
    download_extension()
    accounts = load_accounts()
    if not accounts:
        logger.error("Аккаунты не найдены")
        exit(1)

    threading.Thread(target=status_monitor, args=(accounts,), daemon=True).start()

    with ThreadPoolExecutor(max_workers=CONFIG["MAX_THREADS"]) as executor:
        futures = []
        for idx, account in enumerate(accounts, 1):
            futures.append(executor.submit(worker, account, idx))

        for future in as_completed(futures):
            try: future.result()
            except Exception as e: logger.error(f"Ошибка: {str(e)}")

if __name__ == "__main__":
    main()
