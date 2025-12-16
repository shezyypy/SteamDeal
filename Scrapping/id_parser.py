import requests
import json

def check_id(name):
    url = 'https://api.steampowered.com/ISteamApps/GetAppList/v2/'
    session = requests.Session()
    response = session.get(url)
    src = json.loads(response.text).get("applist").get("apps")

    for game in src:
        if game.get("name") == name:
            id = game.get("appid")
    return id
