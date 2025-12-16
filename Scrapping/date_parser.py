import requests
from bs4 import BeautifulSoup
import re


def get_date(link):
    s = requests.Session()
    s.cookies.set('Steam_Language', 'russian', domain='store.steampowered.com')
    r = s.get(link)
    soup = BeautifulSoup(r.content, 'lxml')

    regex_pattern = r'(\d{1,2})\s+(\w+)'

    offer = soup.find('p', class_='game_purchase_discount_countdown').text
    date = re.search(regex_pattern, offer)[0]
    return date

