import requests
import re
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.wait import WebDriverWait
from selenium import common
from bs4 import BeautifulSoup
from selenium import webdriver
import time
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import json

url = f'https://store.steampowered.com/specials/'
s = Service(executable_path='C:\Пользователи\Олег\PycharmProjects\TelegramBot\chromedriver_win32\chromedriver.exe')
options = webdriver.ChromeOptions()
driver = webdriver.Chrome(service=s, options=options)
driver.get(url)
time.sleep(3)
count = 0
all_sales = []

# def get_date(link):
#     s = requests.Session()
#     s.cookies.set('Steam_Language', 'russian', domain='store.steampowered.com')
#     r = s.get(link)
#     soup = BeautifulSoup(r.content, 'lxml')
#
#     regex_pattern = r'(\d{1,2})\s+(\w+)'
#
#     offer = soup.find('p', class_='game_purchase_discount_countdown').text
#     date = re.search(regex_pattern, offer)[0]
#     return date

def try_sales(driver):
    try:
        for i in range(700):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            wait = WebDriverWait(driver, 10)
            wait.until(ec.element_to_be_clickable((By.XPATH,
                                                   '//div[@class="saleitembrowser_ShowContentsContainer_3IRkb'
                                                   '"]//button[text()="Показать больше"]')))
            driver.find_element(By.XPATH,
                                '//div[@class="saleitembrowser_ShowContentsContainer_3IRkb"]//button[text()="Показать '
                                'больше"]').click()
    except common.exceptions.NoSuchElementException:
        pass
    html = driver.page_source
    soup = BeautifulSoup(html, "lxml")
    games_names = soup.find_all('div', class_='salepreviewwidgets_StoreSaleWidgetRight_1lRFu')
    for game in games_names:
        try:
            name = game.find('div', 'salepreviewwidgets_TitleCtn_1F4bc').find('a').text
            link = game.find('a').get('href')
            price_sale = game.find('div', 'salepreviewwidgets_StoreSalePriceBox_Wh0L8').text
            price_orig = game.find('div', 'salepreviewwidgets_StoreOriginalPrice_1EKGZ').text
            sale = game.find('div', 'salepreviewwidgets_StoreSaleDiscountBox_2fpFv').text
            all_sales.append({'full_name': name, 'price_orig': price_orig, 'sale': sale, 'price_sale': price_sale, 'link': link})
            # date = get_date(link)
            # try:
            #     all_sales.append({'full_name': name, 'price_orig': price_orig, 'sale': sale, 'price_sale': price_sale, 'link': link, 'date': date})
            # except TypeError:
            #     pass
        except AttributeError:
            pass
    with open("all_sales.json", "w", encoding="utf-8") as file:
        json.dump(all_sales, file, indent=4, ensure_ascii=False)
    driver.quit()
    return driver


for i in range(1):
    driver = try_sales(driver)
