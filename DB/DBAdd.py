import psycopg2
from Technical.auth_data import host, password, db_name, user


def add_to_db(id, game):
    global connection
    try:
        connection = psycopg2.connect(host=host, user=user, password=password, database=db_name)
        cursor = connection.cursor()

        with connection.cursor() as cursor:
            cursor.execute(f"INSERT INTO game_users (id_tg, game) VALUES ({id}, '{game}');")
            connection.commit()
    except Exception as _ex:
        print(f'[INFO] Error while working with PostgreSQL {_ex}')
    finally:
        if connection:
            connection.close()
            cursor.close()
            print(f'[INFO] PostgreSQL connection closed')
