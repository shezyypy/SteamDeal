import psycopg2
from auth_data import host, password, db_name, user


def delete_from_db(id, game):
    global connection
    try:
        connection = psycopg2.connect(host=host, user=user, password=password, database=db_name)
        cursor = connection.cursor()

        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM game_users WHERE ctid IN (SELECT ctid FROM game_users WHERE id_tg = {id} AND "
                           f"game = '{game}' LIMIT 1);")
            connection.commit()
    except Exception as _ex:
        print(f'[INFO] Error while working with PostgreSQL {_ex}')
    finally:
        if connection:
            connection.close()
            cursor.close()
            print(f'[INFO] PostgreSQL connection closed')
