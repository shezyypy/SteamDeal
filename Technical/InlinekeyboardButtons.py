from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

ikb = InlineKeyboardMarkup(row_width=2)
ib1 = InlineKeyboardButton(text='Сообщить о проблеме 🚫', callback_data="Error")
ib2 = InlineKeyboardButton(text='Предложить свою идею 💡', callback_data="Idea")
ikb.add(ib1).add(ib2)

PhotoIkb = InlineKeyboardMarkup(row_width=2)
PhotoIkb1 = InlineKeyboardButton(text='Да ✅', callback_data="yes")
PhotoIkb2 = InlineKeyboardButton(text='Нет ❌', callback_data="no")
PhotoIkb.add(PhotoIkb1).add(PhotoIkb2)

PhotoBackIkb = InlineKeyboardMarkup(row_width=1)
PhotoBackIkb1 = InlineKeyboardButton(text="Назад ⬅️", callback_data='back_img')
PhotoBackIkb.add(PhotoBackIkb1)

BaseIkb = InlineKeyboardMarkup(row_width=3)
BaseIkb1 = InlineKeyboardButton(text='Да ✅', callback_data="base_yes")
BaseIkb2 = InlineKeyboardButton(text='Нет ❌', callback_data="base_no")
BackBaseIkb = InlineKeyboardButton(text="Назад ⬅️", callback_data='back_base')
BaseIkb.add(BaseIkb1).add(BaseIkb2).add(BackBaseIkb)

AskIkb = InlineKeyboardMarkup(row_width=2)
AskIkb1 = InlineKeyboardButton(text='Да ✅', callback_data="ask_yes")
AskIkb2 = InlineKeyboardButton(text='Нет ❌', callback_data="ask_no")
AskIkb.add(AskIkb1).add(AskIkb2)

IdOrNameIkb = InlineKeyboardMarkup(row_width=2)
IdOrNameIkb1 = InlineKeyboardButton(text='Айди', callback_data="id")
IdOrNameIkb2 = InlineKeyboardButton(text='Название', callback_data="name")
IdOrNameIkb.add(IdOrNameIkb1).add(IdOrNameIkb2)
