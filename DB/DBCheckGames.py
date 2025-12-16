import psycopg2
from Technical.auth_data import host, password, db_name, user


def check_user_games(id):
    global connection
    try:
        connection = psycopg2.connect(host=host, user=user, password=password, database=db_name)
        cursor = connection.cursor()

        with connection.cursor() as cursor:
            cursor.execute(f"SELECT game FROM game_users WHERE id_tg = {id};")
            connection.commit()
            result = cursor.fetchall()
            end_result = [item[0] for item in result]
            return end_result
    except Exception as _ex:
        print(f'[INFO] Error while working with PostgreSQL: {_ex}')
    finally:
        if connection:
            connection.close()
            cursor.close()
            print(f'[INFO] PostgreSQL connection closed')
