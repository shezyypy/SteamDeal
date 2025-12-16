import requests
from bs4 import BeautifulSoup as BS
import re

id = 1649010
def get_message(id):
    url = f'https://store.steampowered.com/app/{id}'
    s = requests.Session()
    s.cookies.set('Steam_Language', 'russian', domain='store.steampowered.com')
    r = s.get(url)
    soup = BS(r.content, 'lxml')
    regex_pattern = r'(\d{1,2})\s+(\w+)'
    pattern = r'(\d+)\s*pуб\.'
    try:
        name = soup.find('div', class_='apphub_AppName').text
        discount_count = soup.find('div', class_='discount_pct').text
        original_price = soup.find('div', class_='discount_original_price').text
        discount_price = soup.find('div', class_='discount_final_price').text
        link = url
        offer = soup.find('p', class_='game_purchase_discount_countdown').text
        date = re.search(regex_pattern, offer)[0]
        return
    except AttributeError:
        name = soup.find('div', class_='apphub_AppName').text
        price = soup.find('div', class_='game_purchase_price price').text
        match = re.search(pattern, price)
        original_price = match.group(1) + " руб."
        link = url
