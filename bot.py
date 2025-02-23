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

# Кастомный форматтер для логгера с цветной разметкой
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

# Флаг для headless‑режима. Для отладки установите HEADLESS = False.
HEADLESS = True

# Баннер
banner = """
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

# Загрузка переменных окружения
load_dotenv()

# Настройка логгера с цветным форматтером
logger = logging.getLogger()
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = ColoredFormatter("%(asctime)s - %(message)s", "%H:%M:%S")
console_handler.setFormatter(formatter)
logger.handlers = [console_handler]

# Константы
EXTENSION_ID = "caacbgbklghmpodbdafajbgdnegacfmo"
CRX_URL = ("https://clients2.google.com/service/update2/crx?"
           "response=redirect&prodversion=98.0.4758.102&acceptformat=crx2,crx3&"
           "x=id%3D{0}%26uc&nacl_arch=x86-64".format(EXTENSION_ID))
EXTENSION_FILENAME = "app.crx"

def load_accounts():
    """
    Загружает аккаунты.
    Если существует файл accounts.txt, читает его (формат: email:пароль в каждой строке).
    Если файла нет, использует переменные окружения APP_USER и APP_PASS.
    """
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
            logger.error("Не заданы аккаунты. Укажите APP_USER и APP_PASS или создайте файл accounts.txt.")
            exit(1)
    return accounts

# Загрузка прокси из файла active_proxies.txt
with open("active_proxies.txt", "r", encoding="utf-8") as f:
    proxies = [line.strip() for line in f if line.strip()]
if not proxies:
    logger.warning("Прокси не найдены в active_proxies.txt. Работаем в режиме прямого соединения.")
    proxies = [None]

# Инициализация генератора случайных User-Agent
ua = UserAgent()

def create_proxy_auth_extension(host, port, username, password, scheme='http', plugin_path='proxy_auth_plugin.zip'):
    """
    Создает динамическое расширение для Chrome, задающее прокси с аутентификацией.
    Возвращает путь к созданному ZIP-архиву расширения.
    """
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
    chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});
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
        ['blocking']
    );
    """
    with zipfile.ZipFile(plugin_path, 'w') as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)
    return plugin_path

def download_extension():
    """Скачивает расширение для приложения, если оно не скачано."""
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

def setup_chrome_options(proxy=None):
    """
    Настраивает ChromeOptions.
    Если прокси содержит аутентификацию, создается динамическое расширение.
    Добавлены опции для отключения WebRTC (чтобы подавлять ошибки STUN) и уменьшения системного логирования.
    """
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
    # Дополнительные опции для снижения логирования
    chrome_options.add_argument("--disable-logging")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument("--v=0")
    
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
    """
    Производит авторизацию в веб-приложении.
    account: (email, пароль)
    """
    email, password = account
    driver.get("https://app.gradient.network/")
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, '[placeholder="Enter Email"]'))
    )
    driver.find_element(By.CSS_SELECTOR, '[placeholder="Enter Email"]').send_keys(email)
    driver.find_element(By.CSS_SELECTOR, '[type="password"]').send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button").click()
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href="/dashboard/setting"]'))
    )
    logger.info(f"Успешная авторизация для аккаунта: {email}")

def open_extension(driver):
    """Открывает расширение Chrome."""
    driver.get(f"chrome-extension://{EXTENSION_ID}/popup.html")
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.XPATH, '//div[contains(text(), "Status")]'))
    )
    logger.info("Расширение загружено успешно")

def get_chromedriver_path():
    """
    Возвращает путь к ChromeDriver.
    При ошибке очищает кэш и повторяет попытку.
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

def attempt_connection(proxy, account):
    """
    Пытается установить соединение с использованием прокси и аккаунта.
    Если proxy равен None, подключается без прокси.
    Иначе перебирает варианты из списка прокси.
    Возвращает объект driver при успешном подключении или None.
    """
    if proxy is None:
        try:
            chrome_options = setup_chrome_options(None)
            driver_path = get_chromedriver_path()
            driver = webdriver.Chrome(service=Service(driver_path), options=chrome_options)
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
        available_proxies = proxies.copy()
        if proxy in available_proxies:
            available_proxies.remove(proxy)
            available_proxies.insert(0, proxy)
        for pr in available_proxies:
            if pr is None:
                continue
            try:
                chrome_options = setup_chrome_options(pr)
                driver_path = get_chromedriver_path()
                driver = webdriver.Chrome(service=Service(driver_path), options=chrome_options)
                download_extension()
                login_to_app(driver, account)
                open_extension(driver)
                logger.info(f"Подключение успешно с прокси: {pr} для аккаунта {account[0]}")
                return driver
            except Exception as e:
                logger.warning(f"Прокси не сработал: {pr} для аккаунта {account[0]} - Ошибка: {e}")
                try:
                    driver.quit()
                except Exception:
                    pass
                logger.info("Пробуем следующий вариант прокси...")
        logger.error(f"Не удалось установить подключение для аккаунта {account[0]} ни через один из вариантов прокси.")
        return None

def add_account():
    """
    Запрашивает у пользователя данные нового аккаунта и добавляет их в файл accounts.txt.
    """
    email = input("Введите email нового аккаунта: ").strip()
    password = input("Введите пароль для нового аккаунта: ").strip()
    if email and password:
        with open("accounts.txt", "a", encoding="utf-8") as f:
            f.write(f"{email}:{password}\n")
        logger.info(f"Аккаунт {email} успешно добавлен.")
        return (email, password)
    else:
        logger.warning("Неверно введены данные аккаунта.")
        return None

def add_proxy():
    """
    Запрашивает у пользователя ввод нескольких прокси (по одной на строке)
    и добавляет их в файл active_proxies.txt.
    Ввод завершается, когда пользователь вводит пустую строку.
    """
    print("Введите новые прокси (по одной в строке). Для завершения введите пустую строку:")
    new_proxies = []
    while True:
        line = input().strip()
        if not line:
            break
        new_proxies.append(line)
    if new_proxies:
        with open("active_proxies.txt", "a", encoding="utf-8") as f:
            for proxy in new_proxies:
                f.write(proxy + "\n")
        logger.info(f"Прокси добавлены: {', '.join(new_proxies)}")
        return new_proxies
    else:
        logger.warning("Прокси не были введены.")
        return None


def management_interface(accounts):
    """
    Интерфейс управления для выбора аккаунтов и запуска бота.
    Опции:
     1. Запустить бота для одного аккаунта (с прокси)
     2. Запустить бота для одного аккаунта (без прокси)
     3. Запустить бота для всех аккаунтов (с прокси)
     4. Запустить бота для всех аккаунтов (без прокси)
     5. Добавить новый аккаунт
     6. Добавить новый прокси
     7. Выход
     
    При выборе 1 и 2 можно задать количество сессий (нод) для выбранного аккаунта и задержку между запуском нод.
    При выборе 1 также можно указать, использовать ли один прокси для всех нод или разные для каждой.
    """
    while True:
        print("\nМеню управления:")
        print("1. Запустить бота для одного аккаунта (с прокси)")
        print("2. Запустить бота для одного аккаунта (без прокси)")
        print("3. Запустить бота для всех аккаунтов (с прокси)")
        print("4. Запустить бота для всех аккаунтов (без прокси)")
        print("5. Добавить новый аккаунт")
        print("6. Добавить новый прокси")
        print("7. Выход")
        choice = input("Выберите опцию (1-7): ").strip()
        if choice == "1":
            print("\nСписок аккаунтов:")
            for idx, account in enumerate(accounts, start=1):
                print(f"{idx}. {account[0]}")
            try:
                sel = int(input("Выберите номер аккаунта: ").strip())
                if 1 <= sel <= len(accounts):
                    selected_account = accounts[sel - 1]
                    print("\nСписок доступных прокси:")
                    for idx, pr in enumerate(proxies, start=1):
                        print(f"{idx}. {pr if pr else 'Direct mode'}")
                    sel_proxy_input = input("Выберите номер прокси (или оставьте пустым для случайного выбора): ").strip()
                    if sel_proxy_input:
                        sel_proxy = int(sel_proxy_input)
                        if 1 <= sel_proxy <= len(proxies):
                            chosen_proxy = proxies[sel_proxy - 1]
                        else:
                            print("Неверный выбор прокси. Будет использовано случайное прокси.")
                            chosen_proxy = random.choice(proxies)
                    else:
                        chosen_proxy = random.choice(proxies)
                    same_proxy_input = input("Использовать один прокси для всех нод? (да/нет): ").strip().lower()
                    same_proxy = same_proxy_input in ["да", "yes", "y"]
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
                    logger.info(f"Аккаунт {selected_account[0]}: запуск {sessions} сессий с прокси {chosen_proxy if chosen_proxy else 'Direct mode'} с задержкой {delay} сек.")
                    with ThreadPoolExecutor(max_workers=sessions) as executor:
                        for node in range(1, sessions + 1):
                            proxy_for_node = chosen_proxy if same_proxy else random.choice(proxies)
                            executor.submit(worker, selected_account, proxy_for_node, node)
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
                    logger.info(f"Аккаунт {selected_account[0]}: запуск {sessions} сессий без прокси с задержкой {delay} сек.")
                    with ThreadPoolExecutor(max_workers=sessions) as executor:
                        for node in range(1, sessions + 1):
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
                logger.info(f"Запуск бота для всех аккаунтов с прокси. Для каждого аккаунта будет запущено {sessions} сессий с задержкой {delay} сек.")
                with ThreadPoolExecutor(max_workers=min(len(accounts) * sessions, 5)) as executor:
                    futures = []
                    for account in accounts:
                        for node in range(1, sessions + 1):
                            chosen_proxy = random.choice(proxies)
                            futures.append(executor.submit(worker, account, chosen_proxy, node))
                            time.sleep(delay)
                    for future in as_completed(futures):
                        future.result()
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
                logger.info(f"Запуск бота для всех аккаунтов без прокси. Для каждого аккаунта будет запущено {sessions} сессий с задержкой {delay} сек.")
                with ThreadPoolExecutor(max_workers=len(accounts) * sessions) as executor:
                    futures = []
                    for account in accounts:
                        for node in range(1, sessions + 1):
                            futures.append(executor.submit(worker, account, None, node))
                            time.sleep(delay)
                    for future in as_completed(futures):
                        future.result()
            except KeyboardInterrupt:
                logger.info("Остановка всех воркеров по запросу пользователя.")
                break
        elif choice == "5":
            new_acc = add_account()
            if new_acc:
                accounts.append(new_acc)
        elif choice == "6":
            new_proxy = add_proxy()
            if new_proxy:
                proxies.append(new_proxy)
        elif choice == "7":
            print("Выход из программы.")
            exit(0)
        else:
            print("Неверный выбор. Попробуйте снова.")

def add_account():
    """
    Запрашивает у пользователя данные нового аккаунта и добавляет их в файл accounts.txt.
    """
    email = input("Введите email нового аккаунта: ").strip()
    password = input("Введите пароль нового аккаунта: ").strip()
    if email and password:
        with open("accounts.txt", "a", encoding="utf-8") as f:
            f.write(f"{email}:{password}\n")
        logger.info(f"Аккаунт {email} успешно добавлен.")
        return (email, password)
    else:
        logger.warning("Неверно введены данные аккаунта.")
        return None

def add_proxy():
    """
    Запрашивает данные нового прокси и добавляет его в файл active_proxies.txt.
    """
    proxy = input("Введите новый прокси (например, http://username:password@proxy_host:proxy_port): ").strip()
    if proxy:
        with open("active_proxies.txt", "a", encoding="utf-8") as f:
            f.write(proxy + "\n")
        logger.info(f"Прокси {proxy} успешно добавлен.")
        return proxy
    else:
        logger.warning("Прокси не был введён.")
        return None

def main():
    accounts = load_accounts()
    management_interface(accounts)

if __name__ == "__main__":
    main()
