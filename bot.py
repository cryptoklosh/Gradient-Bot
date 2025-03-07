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

# Инициализация colorama для цветного логирования
colorama.init(autoreset=True)

# Конфигурация параметров
CONFIG = {
    "PAGE_LOAD_TIMEOUT": 30,
    "SCRIPT_TIMEOUT": 30,
    "ELEMENT_WAIT_TIMEOUT": 30,
    "EXTENSION_WAIT": 30,
    "STATUS_CHECK_INTERVAL_MIN": 1800,   # 30 минут
    "STATUS_CHECK_INTERVAL_MAX": 5400,   # 90 минут
    "TASK_INTERVAL_MIN": 20,
    "TASK_INTERVAL_MAX": 40,
    "RETRY_INTERVAL": 5,
    "PROXY_RELOAD_INTERVAL": 60,
    "MAX_THREADS": 17,
    "PROXY_THRESHOLD": 10   # Максимальный допустимый ping (сек.)
}

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
| . ` |/ _ \ / _` |    \ \ '__|
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

# Блокировки
proxies_lock = threading.Lock()
chromedriver_lock = threading.Lock()

# Исключение для таймаута рендера расширения
class RendererTimeoutError(Exception):
    pass

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

def update_proxies_file(proxies_list):
    with open("active_proxies.txt", "w", encoding="utf-8") as f:
        for proxy in proxies_list:
            f.write(f"{proxy}\n")
    logger.info("Файл прокси обновлён.")

def reload_proxies(proxies_list):
    if not proxies_list:
        logger.warning("Список прокси пуст. Ждём обновления...")
        time.sleep(CONFIG["PROXY_RELOAD_INTERVAL"])
        return load_proxies()
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

def install_chrome_114():
    logger.info("=== Установка/обновление Google Chrome 114 и ChromeDriver 114 (Linux) ===")
    try:
        os.system("sudo apt-get update")
        os.system("sudo apt-get install -y wget unzip curl")
        cmds = [
            "sudo apt-get remove -y google-chrome-stable google-chrome-beta google-chrome-unstable",
            "sudo apt-get remove -y chromium-browser chromium-chromedriver",
            "sudo snap remove chromium",
            "sudo apt-get autoremove -y"
        ]
        for cmd in cmds:
            os.system(cmd)
        url_chrome = "https://mirror.cs.uchicago.edu/google-chrome/pool/main/g/google-chrome-stable/google-chrome-stable_114.0.5735.90-1_amd64.deb"
        os.system(f"wget -O chrome114.deb {url_chrome}")
        os.system("sudo dpkg -i chrome114.deb")
        os.system("sudo apt-get -f install -y")
        os.system("google-chrome --version || echo 'Google Chrome не установлен'")
        url_driver = "https://chromedriver.storage.googleapis.com/114.0.5735.90/chromedriver_linux64.zip"
        os.system(f"wget -O chromedriver_linux64.zip {url_driver}")
        os.system("unzip -o chromedriver_linux64.zip")
        os.system("sudo chmod +x chromedriver")
        os.system("sudo mv chromedriver /usr/local/bin/")
        os.system("chromedriver --version || echo 'ChromeDriver не установлен'")
        logger.info("Установка/обновление завершена.")
    except Exception as e:
        logger.error(f"Ошибка при установке Chrome/ChromeDriver: {e}")

def check_browser_driver():
    os.system("google-chrome --version || echo 'Google Chrome не установлен'")
    os.system("chromedriver --version || echo 'ChromeDriver не установлен'")

def setup_chrome_options(proxy=None):
    global ua
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
            logger.info(f"Динамическое расширение для прокси создано для: {proxy}")
        else:
            chrome_options.add_argument("--proxy-server=" + proxy)
            logger.info(f"Используется прокси: {proxy}")
    else:
        logger.info("Режим прямого соединения (без прокси).")

    ext_path = Path(EXTENSION_FILENAME).resolve()
    if ext_path.exists():
        chrome_options.add_extension(str(ext_path))
        logger.info("Основное расширение загружено.")
    else:
        logger.warning("Расширение для приложения не найдено.")

    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    return chrome_options

def login_to_app(driver, account):
    if not isinstance(account, tuple) or len(account) < 2:
        logger.error(f"Неверный формат аккаунта: {account}")
        return
    email, password = account
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

def open_extension(driver):
    if HEADLESS:
        logger.info("Headless-режим: пропуск открытия расширения.")
        return
    time.sleep(CONFIG["EXTENSION_WAIT"])
    try:
        driver.get(f"chrome-extension://{EXTENSION_ID}/popup.html")
        WebDriverWait(driver, CONFIG["ELEMENT_WAIT_TIMEOUT"]).until(
            EC.presence_of_element_located((By.XPATH, '//div[contains(text(), "Status")]'))
        )
        logger.info("Расширение загружено успешно")
    except Exception as e:
        logger.warning(f"Не удалось открыть расширение: {e}")

def check_gradient_status(driver):
    try:
        driver.get(f"chrome-extension://{EXTENSION_ID}/popup.html")
        # Устанавливаем таймаут 200 секунд для рендера расширения
        status_element = WebDriverWait(driver, 200).until(
            EC.presence_of_element_located((By.XPATH, '//div[contains(text(), "Status")]'))
        )
        status_text = status_element.text
        logger.info(f"Проверка статуса расширения: {status_text}")
        return status_text
    except Exception as e:
        err_msg = str(e)
        logger.warning(f"Не удалось проверить статус расширения: {err_msg}")
        if "Timed out receiving message from renderer:" in err_msg:
            raise RendererTimeoutError(err_msg)
        return None

def get_chromedriver_path():
    with chromedriver_lock:
        try:
            driver_path = ChromeDriverManager().install()
            return driver_path
        except Exception as e:
            logger.error(f"Ошибка при установке ChromeDriver: {e}. Очищаем кэш и повторяем попытку...")
            cache_dir = os.path.join(os.path.expanduser("~"), ".wdm")
            if os.path.exists(cache_dir):
                try:
                    shutil.rmtree(cache_dir)
                    logger.info("Кэш ChromeDriver удалён.")
                except Exception as e2:
                    logger.error(f"Не удалось удалить кэш ChromeDriver: {e2}")
            driver_path = ChromeDriverManager().install()
            return driver_path

def test_proxy_speed(proxy, test_url="https://www.google.com", timeout=5):
    start = time.time()
    try:
        response = requests.get(test_url, proxies={"http": proxy, "https": proxy}, timeout=timeout)
        elapsed = time.time() - start
        return elapsed
    except Exception as e:
        logger.warning(f"Ошибка при проверке прокси {proxy}: {e}")
        return None

def attempt_connection(proxy, account):
    THRESHOLD = CONFIG["PROXY_THRESHOLD"]
    if proxy is not None:
        elapsed = test_proxy_speed(proxy, timeout=5)
        if elapsed is None or elapsed > THRESHOLD:
            logger.warning(f"Прокси {proxy} слишком медленный (ping = {elapsed} сек.), пропускаем его.")
            return None
    try:
        chrome_options = setup_chrome_options(proxy)
        driver_path = get_chromedriver_path()
        driver = webdriver.Chrome(service=Service(driver_path), options=chrome_options)
        driver.set_page_load_timeout(CONFIG["PAGE_LOAD_TIMEOUT"])
        driver.set_script_timeout(CONFIG["SCRIPT_TIMEOUT"])
        login_to_app(driver, account)
        open_extension(driver)
        logger.info(f"Подключение успешно {'без прокси' if proxy is None else f'с прокси: {proxy}'} для аккаунта {account[0]}")
        return driver
    except Exception as e:
        logger.warning(f"Подключение {'без прокси' if proxy is None else f'с прокси: {proxy}'} не удалось для аккаунта {account[0]} - Ошибка: {e}")
        try:
            driver.quit()
        except Exception:
            pass
        return None

def remove_proxy(proxy, proxies_list):
    with proxies_lock:
        if proxy in proxies_list:
            proxies_list.remove(proxy)
            update_proxies_file(proxies_list)
            logger.info(f"Прокси {proxy} удалён из списка.")

def worker(account, proxies_list, node_index):
    # Устанавливаем случайный интервал проверки статуса от 30 до 90 минут
    status_check_interval = random.uniform(CONFIG["STATUS_CHECK_INTERVAL_MIN"], CONFIG["STATUS_CHECK_INTERVAL_MAX"])
    last_status_check = time.time()
    current_proxy = None
    while True:
        driver = None
        # Пытаемся подключиться с ротацией прокси
        while not driver:
            with proxies_lock:
                if not proxies_list:
                    proxies_list = reload_proxies(proxies_list)
                    if not proxies_list:
                        logger.error(f"Нет рабочих прокси для аккаунта {account[0]}. Ждем перезагрузки...")
                        time.sleep(CONFIG["PROXY_RELOAD_INTERVAL"])
                        continue
                current_proxy = random.choice(proxies_list)
            logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Попытка подключения с прокси: {current_proxy}")
            driver = attempt_connection(current_proxy, account)
            if driver is None:
                remove_proxy(current_proxy, proxies_list)
                logger.info(f"Пробуем следующий прокси для аккаунта {account[0]}")
                time.sleep(CONFIG["RETRY_INTERVAL"])
        logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Подключение установлено, работаем.")
        try:
            while True:
                time.sleep(random.uniform(CONFIG["TASK_INTERVAL_MIN"], CONFIG["TASK_INTERVAL_MAX"]))
                # Если пришло время проверки статуса (между 30 и 90 мин)
                if time.time() - last_status_check >= status_check_interval:
                    try:
                        status = check_gradient_status(driver)
                        last_status_check = time.time()
                        status_check_interval = random.uniform(CONFIG["STATUS_CHECK_INTERVAL_MIN"], CONFIG["STATUS_CHECK_INTERVAL_MAX"])
                        if status is None or "Good" not in status:
                            logger.warning(f"Статус расширения ({status}) не равен 'Good'. Ротация прокси для аккаунта {account[0]}.")
                            break
                    except RendererTimeoutError as rte:
                        # Проверка прокси: если прокси рабочий, не нужно ротацию
                        elapsed = test_proxy_speed(current_proxy, timeout=5) if current_proxy else None
                        if elapsed is not None and elapsed <= CONFIG["PROXY_THRESHOLD"]:
                            logger.warning(f"Ошибка проверки расширения (rte: {rte}), но прокси {current_proxy} работает (ping = {elapsed} сек.). Продолжаем работу.")
                        else:
                            logger.warning(f"Ошибка проверки расширения (rte: {rte}) и прокси {current_proxy} не отвечает. Ротация прокси для аккаунта {account[0]}.")
                            break
                logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Выполнение задач...")
                driver.refresh()
                time.sleep(10)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                logger.info("Имитация активности: страница обновлена и прокручена вниз.")
        except KeyboardInterrupt:
            logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Остановка по запросу пользователя.")
            break
        finally:
            driver.quit()
        # Если статус не удовлетворительный, повторяем подключение с новым прокси

def auto_run_all(accounts, proxies_list):
    max_workers = min(len(accounts), CONFIG["MAX_THREADS"])
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for account in accounts:
            futures.append(executor.submit(worker, account, proxies_list, 1))
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"Ошибка в воркере: {e}")

def main():
    accounts = load_accounts()
    if not accounts:
        logger.error("Аккаунты не найдены. Завершение работы.")
        exit(1)
    proxies_list = load_proxies()
    download_extension()  # Сначала скачиваем расширение без прокси
    auto_run_all(accounts, proxies_list)

if __name__ == "__main__":
    main()