from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.utils.callback_data import CallbackDataFilter

from Technical.auth_data import token, admin_id
from Technical.Class import UserStates, UserPhoto, GameCheck, DeleteGame
from Technical.InlinekeyboardButtons import ikb, PhotoIkb, AskIkb, PhotoBackIkb, IdOrNameIkb
from Scrapping.id_parser import check_id
from DB.DBAdd import add_to_db
from DB.DBCheckGames import check_user_games
from DB.DBDelete import delete_from_db

import json

bot = Bot(token, parse_mode=types.ParseMode.HTML)
dp = Dispatcher(bot, storage=MemoryStorage())
callback_data = CallbackDataFilter("callback_type", "callback_value")

check = 0
n = []

with open('jsons/all_users.json', encoding='utf-8') as file:
    Data = json.load(file)

with open('jsons/all_sales.json', encoding='utf-8') as file:
    Data2 = json.load(file)


@dp.message_handler(commands='start')
async def start(message: types.Message):
    start_buttons = ['Отслеживание скидок 🔍', 'Ваш список 📋']
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(*start_buttons)
    await bot.send_message(message.from_user.id, text="Здравствуйте! Нажмите кнопку снизу, введите название игры и я "
                                                      "буду остлеживать ее цену специально для вас, "
                                                      "шеф 🫡\n\nСписок команд - /help",
                           reply_markup=keyboard)


@dp.message_handler(commands='help')
async def get_help(message: types.Message):
    await bot.send_message(message.from_user.id,
                           "Вот список всех команд:\n"
                           "/start - начало работы\n"
                           "/help - список команд\n"
                           "/feedback - обратная связь\n"
                           "/watcher - отслеживание игры\n"
                           "/list - список отслеживаемых игр\n"
                           )


@dp.message_handler(commands='feedback')
async def feedback(message: types.Message):
    await bot.send_message(message.from_user.id, text='Выберите о чем вы хотите написать', reply_markup=ikb)


@dp.callback_query_handler(lambda query: query.data == "Error", state='*')
async def callback_error(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.edit_message_text(chat_id=callback_query.from_user.id, message_id=callback_query.message.message_id,
                                text='Введите подробное описание проблемы, для ее скорейшего решения! 🚫')
    await UserStates.step1.set()
    await state.update_data({'UserStates': 'step1'})


@dp.callback_query_handler(lambda query: query.data == "Idea", state='*')
async def callback_idea(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.edit_message_text(chat_id=callback_query.from_user.id, message_id=callback_query.message.message_id,
                                text='Введите как можно подробнее свою идею, чтобы мы могли обдумать ее и возможно '
                                     'воплотить в боте! 💡')
    await UserStates.step1.set()
    await state.update_data({'UserStates': 'step1'})


@dp.message_handler(state=UserStates.step1)
async def get_feedback_step2(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['step1'] = message.text
        user_info = data.get('step1') + '\n'

    await bot.send_message(chat_id=admin_id, text=f'Поступило новое сообщение от пользователя '
                                                  f'{message.from_user.full_name}'
                                                  f'(@{message.from_user.username}):\n{user_info}')
    await bot.send_message(chat_id=message.from_user.id, text='Хотите ли вы дополнить ваше сообщение фотографией?',
                           reply_markup=PhotoIkb)
    await UserStates.step2.set()
    await state.update_data({'UserStates': 'step2', 'step1_data': user_info})


@dp.callback_query_handler(lambda query: query.data == "yes", state='*')
async def callback_yes_photo(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.edit_message_text(chat_id=callback_query.from_user.id, message_id=callback_query.message.message_id,
                                text='Пришлите вашу фотографию 🌅', reply_markup=PhotoBackIkb)
    await UserPhoto.photo1.set()
    await state.update_data({'UserPhoto': 'photo1'})


@dp.callback_query_handler(lambda query: query.data == "back_img", state='*')
async def back_image(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.delete_message(callback_query.from_user.id, callback_query.message.message_id)
    await bot.send_message(chat_id=callback_query.from_user.id, text='Хотите ли вы дополнить ваше сообщение '
                                                                     'фотографией?', reply_markup=PhotoIkb)
    await UserStates.step2.set()
    await state.update_data({'UserStates': 'step2'})


@dp.callback_query_handler(lambda query: query.data == "no", state='*')
async def callback_no_photo(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.edit_message_text(chat_id=callback_query.from_user.id, message_id=callback_query.message.message_id,
                                text="Спасибо за подробное описание вашей заявки, это способствует быстрому решению! ❤️"
                                     "")
    await state.finish()


@dp.message_handler(state=UserPhoto.photo1, content_types=['photo'])
async def process_photo(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['photo1_data'] = message.photo[-1].file_id
        user_photo = data.get('photo1_data')
        await bot.send_photo(chat_id=admin_id, photo=user_photo, caption=f"Пользователь {message.from_user.full_name} "
                                                                         f"(@{message.from_user.username})"
                                                                         f" дополнил фотографией")

    await bot.send_message(chat_id=message.chat.id, text="Спасибо за подробное описание вашей заявки, "
                                                         "это способствует быстрому решению! ❤️")
    await state.finish()


@dp.message_handler(lambda message: message.text == "Отслеживание скидок 🔍")
@dp.message_handler(commands='watcher')
async def discount_hunter(message: types.Message):
    await bot.send_message(message.from_user.id, text='Вы хотите вписать название игры или ее ID из Steam?',  reply_markup=IdOrNameIkb)


@dp.callback_query_handler(lambda query: query.data == "id")
async def get_id(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.send_message(callback_query.from_user.id, text='Введите ID интересующей вас игры.\nЕго вы можете узнать с помощью сервиса <a href="https://steamdb.info">SteamDB</a>\nP.S. есть множество других способов)')
    await GameCheck.id.set()
    await state.update_data({'GameCheck': 'id'})


@dp.callback_query_handler(lambda query: query.data == "name")
async def get_name(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.send_message(callback_query.from_user.id, text='Введите название игры, соответсвующее названию в Steam.\nОбязательно соблюдать регистр и знаки препинания!')
    await GameCheck.name.set()
    await state.update_data({'GameCheck': 'name'})


@dp.message_handler(state=GameCheck.name)
async def from_name_to_id(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['name'] = message.text
        name_game = data.get('name')
    try:
        game_id_value = check_id(f'{name_game}')
        await state.update_data(game_id=game_id_value)
        await GameCheck.name_id.set()
    except UnboundLocalError:
        await bot.send_message(message.from_user.id, text='Убедитесь в правильности написания названия вашей игры, '
                                                          'ведь по нашим данным, такой игры нет в Steam.')
        await state.finish()


@dp.message_handler(state=GameCheck.id)
async def id1_handler(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['id'] = message.text
        id = data.get('id')
    await bot.send_message(message.from_user.id, text=id)


@dp.message_handler(state=GameCheck.name_id)
async def id2_handler(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        game_id = data['game_id']
    await bot.send_message(message.from_user.id, text=game_id)
    print("hgdc")


@dp.callback_query_handler(lambda query: query.data == "back_base")
async def back_base(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.delete_message(callback_query.from_user.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, text='Введите название, интересующей вас игры 🎮')
    await GameCheck.name.set()
    await state.update_data({'GameCheck': 'name'})


@dp.callback_query_handler(lambda query: query.data == "base_yes")
async def add_to_base(callback_query: types.CallbackQuery):
    for game in check_user_games(callback_query.from_user.id):
        if n[0] == game:
            await bot.delete_message(callback_query.from_user.id, callback_query.message.message_id)
            await bot.send_message(callback_query.from_user.id, text="Вы уже добавили игру с таким названием, "
                                                                     "вы точно хотите ее добавить еще раз?",
                                   reply_markup=AskIkb)
            break
        else:
            add_to_db(callback_query.from_user.id, n[0])
            n.clear()
            await bot.delete_message(callback_query.from_user.id, callback_query.message.message_id)
            await bot.send_message(callback_query.from_user.id,
                                   text=f'Отлично, игра была добавлена в список. Ждите уведомления! 💋\nЕсли вы хотите '
                                        f'увидеть '
                                        f'список ваших игр, то напишите: /list')


@dp.callback_query_handler(lambda query: query.data == "base_no")
async def delete_from_base(callback_query: types.CallbackQuery):
    n.clear()

    await bot.delete_message(callback_query.from_user.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, text='Надеемся, что вы все-таки решите воспользоваться нашими '
                                                             'услугами! ☺️')


@dp.callback_query_handler(lambda query: query.data == "ask_yes")
async def check_user_game_yes(callback_query: types.CallbackQuery):
    add_to_db(callback_query.from_user.id, n[0])
    n.clear()
    await bot.delete_message(callback_query.from_user.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id,
                           text=f'Отлично, игра была добавлена в список. Ждите уведомления! 💋\nЕсли вы хотите '
                                f'увидеть '
                                f'список ваших игр, то напишите: /list')


@dp.callback_query_handler(lambda query: query.data == "ask_no")
async def check_user_game_no(callback_query: types.CallbackQuery):
    n.clear()

    await bot.delete_message(callback_query.from_user.id, callback_query.message.message_id)
    await bot.send_message(callback_query.from_user.id, text='Тогда мы ждем от вас другую игру! ☺️ Вы можете '
                                                             'перепроверить ваш список с помощью команды /list, '
                                                             'а так же дополнить его с помощью команды /watcher.')


@dp.message_handler(lambda message: message.text == "Ваш список 📋")
@dp.message_handler(commands='list')
async def game_list(message: types.Message, a=1, msg_list=f'Вот список интересующих вас игр: \n'):
    user_list = check_user_games(message.from_user.id)
    if len(user_list) == 0:
        await bot.send_message(message.from_user.id, text="К сожалению ваш список еще пуст, пополните его играми "
                                                          "используя команду /watcher 📝")
    else:
        for game in user_list:
            msg_list = msg_list + f'№{a} {game} \n'
            a += 1
        await bot.send_message(message.from_user.id, text=f'{msg_list}\nВы можете удалить игру с '
                                                          f'помощью команды /delete')


@dp.message_handler(commands='delete')
async def delete_from_list(message: types.Message, state: FSMContext, a=1, msg_list=f'Введите номер игры, которую вы '
                                                                                    f'хотите удалить из списка:\n'):
    user_list = check_user_games(message.from_user.id)
    if len(user_list) == 0:
        await bot.send_message(message.from_user.id, text="К сожалению ваш список еще пуст, пополните его играми "
                                                          "используя команду /watcher 📝")
    else:
        for game in user_list:
            msg_list = msg_list + f'№{a} {game} \n'
            a += 1
        await bot.send_message(message.from_user.id, text=f'{msg_list}')
    await DeleteGame.number.set()
    await state.update_data({'DeleteGame': 'number'})


@dp.message_handler(state=DeleteGame.number)
async def processing_delete(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['number'] = message.text
        user_number = data.get('number')
    await bot.send_message(message.from_user.id, text=f"{check_user_games(message.from_user.id)[int(user_number) - 1]} "
                                                      f"успешно удалена из вашего списка отслеживания!\n"
                                                      f"Можете посмотреть свой список с помощью команды /list")
    delete_from_db(message.from_user.id, check_user_games(message.from_user.id)[int(user_number)-1])
    await state.finish()

'''@dp.message_handler(commands='send')
async def mailing(message: types.Message):
    if message.from_user.id == 1127824573:
        for users in Data:
            for games in Data2:
                if users.get("game") == games.get("full_name"):
                    game = users.get("game")
                    price = games.get("price_orig")
                    discount = games.get("sale")
                    sale = games.get("price_sale")
                    link = games.get("link")
                    date = get_date(link)
                    try:
                        await bot.send_message(users.get("id"), text=f'<a href="{link}">{game}</a> находится на '
                                                                     f'распродаже!🔥\nЕе изначальная '
                                                                     f'стоимость составляла <s>{price}</s>, а с '
                                                                     f'учетом скидки в размере {discount}, '
                                                                     f'ее стоимость составляет <i>{sale}</i>.📉 Скидка '
                                                                     f'будет действовать до {date} ⏳')
                        break
                    except AttributeError:
                        await bot.send_message(users.get("id"), text=f'<a href="{link}">{game}</a> находится на '
                                                                     f'распродаже!🔥\nЕе изначальная '
                                                                     f'стоимость составляла <s>{price}</s>, а с '
                                                                     f'учетом скидки в размере {discount}, '
                                                                     f'ее стоимость составляет <i>{sale}</i>.')
                        break'''


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
