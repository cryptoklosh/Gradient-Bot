import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import random

# Баннер
banner = """
 _   _           _  _____      
| \ | |         | ||____ |     
|  \| | ___   __| |    / /_ __ 
| . ` |/ _ \ / _` |    \ \ '__|
| |\  | (_) | (_| |.___/ / |   
\_| \_/\___/ \__,_|\____/|_|   
                               
ПРОКСИ ЧЕКЕР GRADIENT
"""
print(banner)
time.sleep(1)

# Конфигурация логгера
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger()

# Файл с прокси и файл для активных прокси
PROXY_FILE = "proxies.txt"
ACTIVE_PROXY_FILE = "checked_proxies.txt"

def load_proxies(file_path):
    """
    Загрузка прокси из файла.
    """
    with open(file_path, "r") as f:
        proxies = [line.strip() for line in f if line.strip()]
    return proxies

def save_active_proxies(proxies):
    """
    Сохранение активных прокси в файл.
    """
    with open(ACTIVE_PROXY_FILE, "w") as f:
        f.writelines(f"{proxy}\n" for proxy in proxies)

def check_proxy(proxy):
    """
    Проверка работоспособности прокси для доступа к целевому URL.
    """
    target_url = "https://app.gradient.network/"  
    try:
        response = requests.get(
            target_url,
            proxies={"http": proxy, "https": proxy},
            timeout=10
        )
        if response.status_code == 200:
            logger.info(f"Прокси {proxy} активен")
            return proxy
    except Exception as e:
        logger.warning(f"Прокси {proxy} не прошёл проверку: {e}")
    return None

def run_proxy_checker(proxies):
    """
    Запуск проверки списка прокси.
    """
    logger.info("Запуск проверки прокси...")
    active_proxies = []
    with ThreadPoolExecutor(max_workers=10) as executor:  # Настройка количества потоков
        futures = {executor.submit(check_proxy, proxy): proxy for proxy in proxies}
        for future in as_completed(futures):
            result = future.result()
            if result:
                active_proxies.append(result)
    logger.info(f"Найдено активных прокси: {len(active_proxies)}")
    return active_proxies

def main():
    """
    Основная функция для запуска проверки прокси и последующего выполнения задач.
    """
    # Загрузка прокси
    proxies = load_proxies(PROXY_FILE)
    if not proxies:
        logger.error(f"Прокси не найдены в {PROXY_FILE}")
        return

    # Проверка прокси
    active_proxies = run_proxy_checker(proxies)
    if not active_proxies:
        logger.error("Активные прокси не найдены. Выход...")
        return

    # Сохранение активных прокси
    save_active_proxies(active_proxies)
    logger.info(f"Активные прокси сохранены в {ACTIVE_PROXY_FILE}")

    # Запуск основных задач с активными прокси
    logger.info("Запуск бота с активными прокси...")

if __name__ == "__main__":
    main()