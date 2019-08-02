#!usr/bin/env python3

# imports
import re
import time
import datetime
import string
import os
import argparse
from transliterate import translit

from cheat_api import CheatAPI

#Создаем интерфейс командной строки
cli_parser = argparse.ArgumentParser()
cli_parser.add_argument('-p', '--path', help='path to dialogs folder (default vk_dialogs/')
cli_parser.add_argument('friends', help='list of friends to process', nargs='+')
cli_args = cli_parser.parse_args()

#Путь к каталогу с диалогами для обучения чат-бота
if not cli_args.path:
    DIALOG_PATH = 'vk_dialogs/'
else:
    DIALOG_PATH = cli_args.path

#Регулярки и строки для форматирования текста:
#строка со всеми кириллическими буквами
KIR_LETTERS = 'абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'
#Регулярка для нахождения эмодзи
EMOJI_PATTERN = r'&#\d\d\d\d\d\d;'
#Регулярка для нахождения текстовых смайликов
SMILE_PATTERN = r'(\s*\)+\.*)|(\s*=\)+\.*)|(\s*\(+\.*)|(\s*=\(+\.*)'
#Регулярка для хддд смайликов
XD_SMILE_PATTERN = r'(х|Х)+(д|Д)+'
#регулярка для o_o смайла
O_O_SMILE_PATTERN = r'(о|О|o|O|0)(_|\.)+(о|О|o|O|0)'
#Регулярка для нахождения ulr адресов
URL_PATTERN = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\), ]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
#Регулярка для нахождения слов, написанных не в той раскладке
WRONG_LAYOUT_PATTERN = r'''[a-z\[\]{};':\"\,\.\<\>\/\?\\]+'''

#Настройки для разбиения истории сообщений на диалоги
#Временной промежуток между двумя сообщениями, после которого можно создать новый диалог
DIALOG_INTERVAL = 12*60*60
#Временной промежуток между сообщениями, чтобы они считались частью одного диалога
MESSAGE_INTERVAL = 60*5
#Максимальное количество символов в тексте сообщения (чтобы отсекать простыни и мои страдашки по бывшим)
MESSAGE_MAX_LENGHT = 200

#Класс разговора
class Conversation():
    def __init__(self):
        self.message_list = []
    def add_message(self, message):
        self.message_list.append(message)
    def get_last_message_time(self):
        if self.message_list:
            return self.message_list[-1].time
        else:
            return None
    def get_last_message_author(self):
        if self.message_list:
            return self.message_list[-1].author
        else:
            return 0
    def add_text_to_last_message(self, message):
        additional_text = message.text
        self.message_list[-1].add_text(additional_text)
    def is_empty(self):
        if self.message_list:
            return False
        else:
            return True
  

#Класс сообщения
class Message():
    #Статический метод для форматиования текста сообщения
    @staticmethod
    def format_message_text(text):
        #Избавляемся от эмодзи
        formatted_text = re.sub(EMOJI_PATTERN, '', text)
        #Избавляемся от ненужных символов переноса строки (зампеняем их пробелами)
        formatted_text = formatted_text.replace('\n', ' ')
        #Убираем текстовые майлы и заменяем их точками
        formatted_text = re.sub(SMILE_PATTERN, '', formatted_text)
        formatted_text = re.sub(O_O_SMILE_PATTERN, '', formatted_text)
        formatted_text = re.sub(XD_SMILE_PATTERN, '', formatted_text)
        #заменяем существующие двойные кавычки одинарными:
        formatted_text = formatted_text.replace('"', "'")
        #Заменяем везде обраный слеш на нормальный (представил себе эту фразу в контексте фанфиков с фикбука)
        formatted_text = formatted_text.replace('\\', '/')
        #Убираем из сообщений urlы
        formatted_text = re.sub(URL_PATTERN, '', formatted_text)
        #Очищаем текст от слов, случайно написанных не в той раскладке:
        for word in formatted_text.split(' '):
            #Если в слове все буквы латинские или знаки пунктуации, удаляем
            if set(word).issubset(set(string.ascii_letters)):
                formatted_text = re.sub(word, '', formatted_text)
        #В финале убираем пробелы с конца и с начала строки
        formatted_text = formatted_text.strip()
        return(formatted_text)

    def __init__(self, author, text, time):
        self.author = author
        self.time = time
        #Заключаем текст каждого сообщения в двойные кавычки
        self.text = '"{}"'.format(self.format_message_text(text))

    def add_text(self, text):
        if self.text.strip('"'):
            self.text = '"{}. {}"'.format(self.text.strip('"'), text.strip('"'))
        else:
            self.text = text


#Функция для очистки массива сообщений и разбиения на отдельные разговоры
def clean_conversation(raw_messages_list):
    conversation = Conversation() #Создаем объект разговора
    conversations_list = [] #массив разговоровре
    last_processed_message_time = 0
    for raw_message in raw_messages_list:
        author = raw_message['from_id']
        time = raw_message['date']
        text = raw_message['text']

        # проверка есть ли в сообщении вложения
        if raw_message['attachments'] or raw_message['fwd_messages']:
            attachments = True
        else:
            attachments = False

        #Блок инструкция для всех сообщений кроме первого
        if last_processed_message_time:

            #1й случай - время между сообщениями меньше максимального временного промежутка между 
            #последовательными сообщениями в диалоге
            if (time - last_processed_message_time) < DIALOG_INTERVAL:
                #Если к сообщению прикреплено вложение
                if attachments:
                    #Если диалог не пустой, добавляем диалог в список диалогов, создаем новый диалог
                    #обновляем время последнего сообщения
                    if not conversation.is_empty():
                        conversations_list.append(conversation)
                        conversation = Conversation()
                        last_processed_message_time = time
                        continue
                    #Если диалог пустой обновляем время последнего сообщения
                    else:
                        last_processed_message_time = time
                        continue
                #Если к сообщению не прикреплено вложение
                else:
                    message = Message(author, text, time)
                    #Если автор сообщения тот же, что и у предыдущего - добавляем текст к предыдущему сообщению
                    if author == conversation.get_last_message_author():
                        conversation.add_text_to_last_message(message)
                    # если автор другой, добавляем сообщение в диалог
                    elif message.text.strip('"'):
                        conversation.add_message(message)
                        last_processed_message_time = time
                    continue

            #2й случай - если время между сообщениями меньше временного промежутка между диалогами, но больше 
            #временного промежутка между сообщениями пропускаем сообщение и обновляем время
            #последнего сообщения
            if MESSAGE_INTERVAL < (time - last_processed_message_time) < DIALOG_INTERVAL:
                last_processed_message_time = time
                continue

            #3й случай - время между двумя сообщениями больше временного промежутка между диалогами
            if (time - last_processed_message_time) > DIALOG_INTERVAL:
                #Если к сообщению прикреплено вложение пропускаем сообщение и обновляем время последнего сообщения
                if attachments:
                    last_processed_message_time = time
                    continue
                #Если в сообщении нет вложения 
                else:
                    #Если текущий диалог не пуст, завершаем диалог, добавляем его в список и создаем пустой диалог
                    if not conversation.is_empty():
                        conversations_list.append(conversation)
                        conversation = Conversation()
                    #Создаем объект сообщения и добавляем его в диалог, если текст после форматирования не пустой
                    message = Message(author, text, time)
                    if message.text.strip('"'):
                        conversation.add_message(message)
                    last_processed_message_time = time

        #Блок инструкция для первого сообщения
        else:
            # если к сообщению прикреплено вложение, игнорируем сообщение
            if attachments:
                continue
            # если вложение не прикреплено, добавляем сообщение в диалог
            message = Message(author, text, time)
            conversation.add_message(message)
            last_processed_message_time = time

    #При возвращении списка бесед отсекаем первую беседу в списке (Так как самое первое сообщение может быть вырвано из контекста)
    return conversations_list[1:]
        

#функция для создания файла диалога
def create_dialog_file(filename):
    if '{}.yml'.format(filename) in os.listdir(path=DIALOG_PATH):
        print('rewriting')
        os.remove('{}{}.yml'.format(DIALOG_PATH, filename))
    with open('{}{}.yml'.format(DIALOG_PATH, filename), 'a') as dialogs_file:
        dialogs_file.write('categories:\n- conversations\nconversations:\n')    

#функция для записи разговоров в файл
def write_conversations(conversations_list, filename):
    with open('{}{}.yml'.format(DIALOG_PATH, filename), 'a') as dialogs_file:
        for conversation in conversations_list:
            if len(conversation.message_list) == 1:
                continue
            for i in range(len(conversation.message_list)):
                message = conversation.message_list[i]
                if i == 0:
                    dialogs_file.write('- - {}\n'.format(message.text))
                else:
                    dialogs_file.write('  - {}\n'.format(message.text))

#Функция для получения и записи всей истории сообщений
def process_dialog(name):
    #Проверяем, создана ли папка dialogs, если нет - создаем
    try:
        os.mkdir('vk_dialogs')
    except OSError:
        pass
    cheat_api = CheatAPI('YOUR_VK_LOGIN','YOUR_VK_PASSWORD')
    #Получаем список друзей
    friends = cheat_api.method('friends.get', count=205, fields = 'domain')['response']['items']
    #Получаем имя и фамилию друга
    first_name, last_name = name.split()
    print(first_name)
    print(last_name)
    #Ищем id этого друга в спске друзей
    friend_id = None
    for friend in friends:
        print(type(friend))
        if friend['first_name'] == first_name and friend['last_name'] == last_name:
            friend_id = friend['id']
            break
    else:
        print('не нашли')
        return 0
    #Если мы уже создавали такой файл, стираем его
    filename = translit('{}_{}'.format(first_name, last_name), reversed=True)
    create_dialog_file(filename)
    messages_count = cheat_api.method('messages.getHistory', count=1, peer_id=friend_id)['response']['count']
    start_message_id = -1
    offset = 0
    request_number = 0
    requests_count = messages_count//200
    while True:
        try:
            history_part = cheat_api.method('messages.getHistory', count=200, peer_id=friend_id, offset=offset, start_message_id=start_message_id)['response']['items']
            if len(history_part) == 1:
                break
            print('успех запроса №{} из {}'.format(request_number, requests_count))
            request_number += 1
            start_message_id = history_part[-1]['id']
            conversations_list = clean_conversation(history_part[::-1])
            write_conversations(conversations_list, filename)
            time.sleep(0.5)
        except Exception as e:
            print('не удалось выполнить запрос №{}'.format(request_number))
            print(e)
            break

#Функция для обработки некольких друзей
def process_friends(friends):
    for friend in friends:
        print('Начинаю обработку диалога с другом {}'.format(friend))
        process_dialog(friend)


if __name__ == '__main__':
    friends = cli_args.friends
    process_friends(friends)
