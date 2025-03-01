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
import colorama
from colorama import Fore, Style

# Инициализация colorama для цветного логирования
colorama.init(autoreset=True)

# Кастомный форматтер для логов с цветами
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

# Флаг для headless-режима. Если требуется работать с UI расширения – установите HEADLESS = False.
HEADLESS = False

# Баннер (raw-строка для корректного отображения обратных слешей)
banner = r"""
 _   _           _  _____      
| \ | |         | ||____ |     
|  \| | ___   __| |    / /_ __ 
| . ` |/ _ \ / _` |    \ \ '__|
| |\  | (_) | (_| |.___/ / |   
\_| \_/\___/ \__,_|\\____/|_|   
                               
Менеджер Gradient Bot
    @nod3r - Мультиаккаунт версия
"""
print(banner)
time.sleep(1)

# Загрузка переменных окружения из .env
load_dotenv()

# Настройка логгера с цветным форматтером
logger = logging.getLogger()
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = ColoredFormatter("%(asctime)s - %(message)s", "%H:%M:%S")
console_handler.setFormatter(formatter)
logger.handlers = [console_handler]

# Константы для расширения приложения
EXTENSION_ID = "caacbgbklghmpodbdafajbgdnegacfmo"
CRX_URL = ("https://clients2.google.com/service/update2/crx?"
           "response=redirect&prodversion=98.0.4758.102&acceptformat=crx2,crx3&"
           "x=id%3D{0}%26uc&nacl_arch=x86-64".format(EXTENSION_ID))
EXTENSION_FILENAME = "app.crx"

def load_accounts():
    """Загружает аккаунты из файла accounts.txt."""
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
    """Загружает прокси из файла active_proxies.txt."""
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

def save_accounts(new_accounts):
    """Добавляет новые аккаунты в файл accounts.txt."""
    with open("accounts.txt", "a", encoding="utf-8") as f:
        for account in new_accounts:
            f.write(f"{account[0]}:{account[1]}\n")
    logger.info(f"Добавлено {len(new_accounts)} новых аккаунтов в accounts.txt.")

def save_proxies(new_proxies):
    """Добавляет новые прокси в файл active_proxies.txt."""
    with open("active_proxies.txt", "a", encoding="utf-8") as f:
        for proxy in new_proxies:
            f.write(f"{proxy}\n")
    logger.info(f"Добавлено {len(new_proxies)} новых прокси в active_proxies.txt.")

# Глобальный генератор User-Agent
ua = UserAgent()

def create_proxy_auth_extension(host, port, username, password, scheme='http', plugin_path='proxy_auth_plugin.zip'):
    """Создает динамическое расширение для Chrome, задающее прокси с аутентификацией."""
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
    """Скачивает расширение, если оно не скачано или устарело."""
    logger.info(f"Скачивание расширения с: {CRX_URL}")
    ext_path = Path(EXTENSION_FILENAME)
    if ext_path.exists() and time.time() - ext_path.stat().st_mtime < 86400:
        logger.info("Расширение уже скачано, пропускаем скачивание...")
        return
    response = requests.get(CRX_URL, headers={"User-Agent": ua.random})
    if response.status_code == 200:
        ext_path.write_bytes(response.content)
        logger.info("Расширение успешно скачано")
    else:
        logger.error(f"Не удалось скачать расширение: {response.status_code}")
        exit(1)

def install_chrome_114():
    """
    Устанавливает необходимые утилиты, удаляет старые версии,
    скачивает и устанавливает Google Chrome 114 и ChromeDriver 114.
    """
    logger.info("=== Установка/обновление Google Chrome 114 и ChromeDriver 114 ===")
    try:
        logger.info("Обновляем списки пакетов и устанавливаем wget, unzip и curl...")
        os.system("sudo apt-get update")
        os.system("sudo apt-get install -y wget unzip curl")
        
        logger.info("Удаляем старые версии Chrome и Chromium...")
        cmds = [
            "sudo apt-get remove -y google-chrome-stable google-chrome-beta google-chrome-unstable",
            "sudo apt-get remove -y chromium-browser chromium-chromedriver",
            "sudo snap remove chromium",
            "sudo apt-get autoremove -y"
        ]
        for cmd in cmds:
            os.system(cmd)
        
        logger.info("Скачиваем Google Chrome 114...")
        url_chrome = "https://mirror.cs.uchicago.edu/google-chrome/pool/main/g/google-chrome-stable/google-chrome-stable_114.0.5735.90-1_amd64.deb"
        os.system(f"wget -O chrome114.deb {url_chrome}")
        logger.info("Устанавливаем Google Chrome 114...")
        os.system("sudo dpkg -i chrome114.deb")
        os.system("sudo apt-get -f install -y")
        logger.info("Проверка версии Google Chrome:")
        os.system("google-chrome --version || echo 'Google Chrome не установлен'")
        
        logger.info("Скачиваем ChromeDriver 114...")
        url_driver = "https://chromedriver.storage.googleapis.com/114.0.5735.90/chromedriver_linux64.zip"
        os.system(f"wget -O chromedriver_linux64.zip {url_driver}")
        logger.info("Распаковываем ChromeDriver...")
        os.system("unzip -o chromedriver_linux64.zip")
        logger.info("Делаем chromedriver исполняемым и перемещаем в /usr/local/bin...")
        os.system("sudo chmod +x chromedriver")
        os.system("sudo mv chromedriver /usr/local/bin/")
        logger.info("Проверка версии ChromeDriver:")
        os.system("chromedriver --version || echo 'ChromeDriver не установлен'")
        
        logger.info("Установка/обновление завершена.")
    except Exception as e:
        logger.error(f"Ошибка при установке Chrome/ChromeDriver: {e}")

def check_browser_driver():
    """Проверяет установленные Google Chrome и ChromeDriver."""
    logger.info("=== Проверка установленных Google Chrome и ChromeDriver ===")
    os.system("google-chrome --version || echo 'Google Chrome не установлен'")
    os.system("chromedriver --version || echo 'ChromeDriver не установлен'")

def test_proxy_speed(proxy, test_url="https://www.google.com", timeout=5):
    """
    Проверяет время отклика прокси.
    Возвращает время ответа или None, если запрос не удался.
    """
    try:
        start = time.time()
        response = requests.get(test_url, proxies={"http": proxy, "https": proxy}, timeout=timeout)
        elapsed = time.time() - start
        return elapsed
    except Exception as e:
        logger.warning(f"Ошибка при проверке прокси {proxy}: {e}")
        return None

def setup_chrome_options(proxy=None):
    """
    Настраивает ChromeOptions, включая опции для WebRTC, расширений и дополнительных флагов.
    """
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
    
    if proxy:
        if "@" in proxy:
            parsed = urlparse(proxy)
            scheme = parsed.scheme
            username = parsed.username
            password = parsed.password
            host = parsed.hostname
            port = parsed.port
            plugin_path = create_proxy_auth_extension(host, port, username, password, scheme)
            chrome_options.add_extension(plugin_path)
            logger.info(f"Динамическое расширение для прокси создано для: {proxy}")
        else:
            chrome_options.add_argument("--proxy-server=" + proxy)
            logger.info(f"Используется прокси: {proxy}")
        logger.info("Прокси используется, пропускаем загрузку основного расширения приложения.")
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
    """
    Производит авторизацию в веб-приложении.
    account: (email, пароль)
    """
    email, password = account
    driver.get("https://app.gradient.network/")
    WebDriverWait(driver, 300).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, '[placeholder="Enter Email"]'))
    )
    driver.find_element(By.CSS_SELECTOR, '[placeholder="Enter Email"]').send_keys(email)
    driver.find_element(By.CSS_SELECTOR, '[type="password"]').send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button").click()
    WebDriverWait(driver, 300).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href="/dashboard/setting"]'))
    )
    logger.info(f"Успешная авторизация для аккаунта: {email}")

def open_extension(driver, max_retries=3):
    """
    Открывает расширение Gradient (Chrome) с несколькими попытками.
    Если HEADLESS=False, пытается открыть страницу расширения.
    """
    if HEADLESS:
        logger.info("Headless-режим: пропуск открытия расширения.")
        return
    for attempt in range(1, max_retries + 1):
        time.sleep(10)
        try:
            driver.get(f"chrome-extension://{EXTENSION_ID}/popup.html")
            WebDriverWait(driver, 300).until(
                EC.presence_of_element_located((By.XPATH, '//div[contains(text(), "Status")]'))
            )
            logger.info("Расширение загружено успешно")
            return
        except Exception as e:
            logger.warning(f"Попытка {attempt} открытия расширения не удалась: {e}")
    logger.error("Не удалось открыть расширение после нескольких попыток")
    raise Exception("Не удалось открыть расширение")

def get_chromedriver_path():
    """
    Возвращает путь к ChromeDriver с помощью webdriver-manager.
    """
    try:
        driver_path = ChromeDriverManager().install()
        return driver_path
    except Exception as e:
        logger.error(f"Ошибка при установке ChromeDriver: {e}. Очищаем кэш и повторяем попытку...")
        cache_dir = os.path.join(os.path.expanduser("~"), ".wdm")
        if os.path.exists(cache_dir):
            import shutil
            shutil.rmtree(cache_dir)
            logger.info("Кэш ChromeDriver удалён.")
        driver_path = ChromeDriverManager().install()
        return driver_path

def check_browser_driver():
    """Проверяет установленные Google Chrome и ChromeDriver."""
    logger.info("=== Проверка установленных Google Chrome и ChromeDriver ===")
    os.system("google-chrome --version || echo 'Google Chrome не установлен'")
    os.system("chromedriver --version || echo 'ChromeDriver не установлен'")

def attempt_connection(proxy, account):
    """
    Пытается установить соединение с использованием прокси и аккаунта.
    Если прокси слишком медленный, пропускает его.
    """
    THRESHOLD = 10  # максимальное допустимое время отклика (сек.)
    if proxy is not None:
        elapsed = test_proxy_speed(proxy, timeout=5)
        if elapsed is None or elapsed > THRESHOLD:
            logger.warning(f"Прокси {proxy} слишком медленный (ping = {elapsed} сек.), пропускаем его.")
            return None

    if proxy is None:
        try:
            chrome_options = setup_chrome_options(None)
            driver_path = get_chromedriver_path()
            driver = webdriver.Chrome(service=Service(driver_path), options=chrome_options)
            driver.set_page_load_timeout(300)
            driver.set_script_timeout(300)
            download_extension()
            login_to_app(driver, account)
            open_extension(driver)
            logger.info(f"Подключение успешно без прокси для аккаунта {account[0]}")
            return driver
        except Exception as e:
            logger.warning(f"Подключение без прокси не удалось для аккаунта {account[0]} - Ошибка: {e}")
            try:
                driver.quit()
            except Exception:
                pass
            return None
    else:
        try:
            chrome_options = setup_chrome_options(proxy)
            driver_path = get_chromedriver_path()
            driver = webdriver.Chrome(service=Service(driver_path), options=chrome_options)
            driver.set_page_load_timeout(300)
            driver.set_script_timeout(300)
            download_extension()
            login_to_app(driver, account)
            open_extension(driver)
            logger.info(f"Подключение успешно с прокси: {proxy} для аккаунта {account[0]}")
            return driver
        except Exception as e:
            logger.warning(f"Прокси не сработал: {proxy} для аккаунта {account[0]} - Ошибка: {e}")
            try:
                driver.quit()
            except Exception:
                pass
            logger.info("Пробуем следующий вариант прокси...")
        logger.error(f"Не удалось установить соединение для аккаунта {account[0]} ни через один из вариантов прокси.")
        return None

def simulate_activity(driver):
    """
    Имитация активности: периодическое обновление страницы и прокрутка вниз.
    Это помогает поддерживать сессию активной.
    """
    try:
        while True:
            # Каждые 2 минуты обновляем страницу и прокручиваем вниз
            time.sleep(120)
            driver.refresh()
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            logger.info("Имитация активности: страница обновлена и прокручена вниз.")
    except Exception as e:
        logger.error(f"Ошибка при имитации активности: {e}")

def worker(account, proxy, node_index):
    """
    Функция-воркер для выполнения задач для конкретного аккаунта.
    """
    driver = attempt_connection(proxy, account)
    if driver:
        logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Работаем.")
        # Запускаем отдельный поток для имитации активности
        activity_thread = None
        try:
            import threading
            activity_thread = threading.Thread(target=simulate_activity, args=(driver,), daemon=True)
            activity_thread.start()
            while True:
                time.sleep(random.uniform(20, 40))
                logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Выполнение задач...")
                # Здесь можно добавить дополнительные действия (клики, mouse events и т.д.)
        except KeyboardInterrupt:
            logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Остановка по запросу пользователя.")
        finally:
            driver.quit()
    else:
        logger.info(f"Аккаунт {account[0]} - Нода {node_index}: Не удалось подключиться. Переход к следующему.")

def add_accounts_interactive():
    """Интерактивное добавление новых аккаунтов."""
    print("\nВведите новые аккаунты (формат: email:пароль, один на строку). Пустая строка для завершения:")
    new_accounts = []
    while True:
        entry = input().strip()
        if not entry:
            break
        if ":" in entry:
            parts = entry.split(":")
            email = parts[0].strip()
            password = ":".join(parts[1:]).strip()
            if email and password:
                new_accounts.append((email, password))
        else:
            print("Неверный формат. Используйте email:пароль.")
    if new_accounts:
        save_accounts(new_accounts)
    else:
        print("Нет новых аккаунтов для добавления.")

def add_proxies_interactive():
    """Интерактивное добавление новых прокси."""
    print("\nВведите новые прокси (один на строку). Пустая строка для завершения:")
    new_proxies = []
    while True:
        entry = input().strip()
        if not entry:
            break
        new_proxies.append(entry)
    if new_proxies:
        save_proxies(new_proxies)
    else:
        print("Нет новых прокси для добавления.")

def management_interface(accounts):
    global proxies
    # Ограничение: 1 аккаунт = 1 прокси
    if len(proxies) < len(accounts):
        logger.error("Недостаточно прокси для всех аккаунтов. Требуется 1 прокси на аккаунт.")
        exit(1)
    # Создаем словарь сопоставления: каждому аккаунту назначается прокси по порядку
    unique_mapping = {account[0]: proxies[i] for i, account in enumerate(accounts)}
    logger.info("Уникальное сопоставление аккаунт -> прокси:")
    for acc, prox in unique_mapping.items():
        logger.info(f"  {acc} -> {prox}")

    # Автоматический запуск одной сессии для каждого аккаунта с назначенным прокси
    with ThreadPoolExecutor(max_workers=len(accounts)) as executor:
        for account in accounts:
            assigned_proxy = unique_mapping[account[0]]
            executor.submit(worker, account, assigned_proxy, 1)

    # Далее интерактивное меню управления
    while True:
        print("\nМеню управления:")
        print("1. Запустить бота для одного аккаунта (с прокси)")
        print("2. Запустить бота для одного аккаунта (без прокси)")
        print("3. Запустить бота для всех аккаунтов (с прокси)")
        print("4. Запустить бота для всех аккаунтов (без прокси)")
        print("5. Добавить новые аккаунты")
        print("6. Добавить новые прокси")
        print("7. Проверить/Установить Google Chrome и ChromeDriver")
        print("8. Выход")
        choice = input("Выберите опцию (1-8): ").strip()
        if choice == "1":
            print("\nСписок аккаунтов:")
            for idx, account in enumerate(accounts, start=1):
                print(f"{idx}. {account[0]}")
            try:
                sel = int(input("Выберите номер аккаунта: ").strip())
                if 1 <= sel <= len(accounts):
                    selected_account = accounts[sel - 1]
                    # Используем назначенный прокси
                    chosen_proxy = unique_mapping[selected_account[0]]
                    sessions_input = input("Введите количество сессий (нод) для данного аккаунта (1 для ограничения): ").strip()
                    try:
                        sessions = int(sessions_input)
                    except ValueError:
                        sessions = 1
                        print("Неверное значение. Будет запущена 1 сессия.")
                    delay_input = input("Введите задержку между запуском нод (в секундах): ").strip()
                    try:
                        delay = float(delay_input)
                    except ValueError:
                        delay = 0
                        print("Неверное значение. Задержка установлена в 0 секунд.")
                    logger.info(f"Аккаунт {selected_account[0]}: запуск {sessions} сессии(й) с прокси {chosen_proxy} с задержкой {delay} сек.")
                    with ThreadPoolExecutor(max_workers=sessions) as executor:
                        for node in range(1, sessions+1):
                            executor.submit(worker, selected_account, chosen_proxy, node)
                            time.sleep(delay)
                else:
                    print("Неверный номер аккаунта.")
            except ValueError:
                print("Пожалуйста, введите корректное число.")
        elif choice == "2":
            print("\nЗапуск бота без прокси для одного аккаунта.")
            print("\nСписок аккаунтов:")
            for idx, account in enumerate(accounts, start=1):
                print(f"{idx}. {account[0]}")
            try:
                sel = int(input("Выберите номер аккаунта: ").strip())
                if 1 <= sel <= len(accounts):
                    selected_account = accounts[sel - 1]
                    sessions_input = input("Введите количество сессий (нод) для данного аккаунта: ").strip()
                    try:
                        sessions = int(sessions_input)
                    except ValueError:
                        sessions = 1
                        print("Неверное значение. Будет запущена 1 сессия.")
                    delay_input = input("Введите задержку между запуском нод (в секундах): ").strip()
                    try:
                        delay = float(delay_input)
                    except ValueError:
                        delay = 0
                        print("Неверное значение. Задержка установлена в 0 секунд.")
                    logger.info(f"Аккаунт {selected_account[0]}: запуск {sessions} сессии(й) без прокси с задержкой {delay} сек.")
                    with ThreadPoolExecutor(max_workers=sessions) as executor:
                        for node in range(1, sessions+1):
                            executor.submit(worker, selected_account, None, node)
                            time.sleep(delay)
                else:
                    print("Неверный номер аккаунта.")
            except ValueError:
                print("Пожалуйста, введите корректное число.")
        elif choice == "3":
            try:
                sessions_input = input("Введите количество сессий (нод) для каждого аккаунта: ").strip()
                try:
                    sessions = int(sessions_input)
                except ValueError:
                    sessions = 1
                    print("Неверное значение. Будет запущена 1 сессия для каждого аккаунта.")
                delay_input = input("Введите задержку между запуском нод (в секундах): ").strip()
                try:
                    delay = float(delay_input)
                except ValueError:
                    delay = 0
                    print("Неверное значение. Задержка установлена в 0 секунд.")
                logger.info(f"Запуск бота для всех аккаунтов с прокси. Для каждого аккаунта будет запущено {sessions} сессии(й) с задержкой {delay} сек.")
                with ThreadPoolExecutor(max_workers=len(accounts)) as executor:
                    for account in accounts:
                        assigned_proxy = unique_mapping[account[0]]
                        for node in range(1, sessions+1):
                            executor.submit(worker, account, assigned_proxy, node)
                            time.sleep(delay)
            except KeyboardInterrupt:
                logger.info("Остановка всех воркеров по запросу пользователя.")
                break
        elif choice == "4":
            try:
                sessions_input = input("Введите количество сессий (нод) для каждого аккаунта: ").strip()
                try:
                    sessions = int(sessions_input)
                except ValueError:
                    sessions = 1
                    print("Неверное значение. Будет запущена 1 сессия для каждого аккаунта.")
                delay_input = input("Введите задержку между запуском нод (в секундах): ").strip()
                try:
                    delay = float(delay_input)
                except ValueError:
                    delay = 0
                    print("Неверное значение. Задержка установлена в 0 секунд.")
                logger.info(f"Запуск бота для всех аккаунтов без прокси. Для каждого аккаунта будет запущено {sessions} сессии(й) с задержкой {delay} сек.")
                with ThreadPoolExecutor(max_workers=len(accounts)) as executor:
                    for account in accounts:
                        for node in range(1, sessions+1):
                            executor.submit(worker, account, None, node)
                            time.sleep(delay)
            except KeyboardInterrupt:
                logger.info("Остановка всех воркеров по запросу пользователя.")
                break
        elif choice == "5":
            add_accounts_interactive()
            accounts[:] = load_accounts()
            if len(proxies) < len(accounts):
                logger.error("Недостаточно прокси для всех аккаунтов после добавления!")
                exit(1)
            unique_mapping = {account[0]: proxies[i] for i, account in enumerate(accounts)}
        elif choice == "6":
            add_proxies_interactive()
            proxies[:] = load_proxies()
            if len(proxies) < len(accounts):
                logger.error("Недостаточно прокси для всех аккаунтов после добавления!")
                exit(1)
            unique_mapping = {account[0]: proxies[i] for i, account in enumerate(accounts)}
        elif choice == "7":
            print("\nМеню проверки и установки браузера и драйвера:")
            print("1. Проверить установленные Google Chrome и ChromeDriver")
            print("2. Установить Google Chrome 114 и ChromeDriver 114")
            sub_choice = input("Выберите опцию (1-2): ").strip()
            if sub_choice == "1":
                check_browser_driver()
            elif sub_choice == "2":
                install_chrome_114()
            else:
                print("Неверный выбор.")
        elif choice == "8":
            print("Выход из программы.")
            exit(0)
        else:
            print("Неверный выбор. Попробуйте снова.")

def main():
    accounts = load_accounts()
    global proxies
    proxies = load_proxies()
    # Ограничение: 1 аккаунт = 1 прокси
    if len(proxies) < len(accounts):
        logger.error("Недостаточно прокси для всех аккаунтов. Требуется 1 прокси на аккаунт.")
        exit(1)
    # Создаем mapping уникальное сопоставление аккаунт->прокси
    global unique_mapping
    unique_mapping = {account[0]: proxies[i] for i, account in enumerate(accounts)}
    management_interface(accounts)

if __name__ == "__main__":
    main()
