# Загрузка обоев на Google Диск

Загрузчик берёт готовые JPEG и `sent_prompt` из манифеста пилота. `prompts.csv` содержит столбцы `filename`, `sent_prompt`, `model` в UTF-8. Генерация и изменение JPEG в этот запуск не входят.

## Первое подключение

1. В своём проекте Google Cloud включите Google Drive API и настройте экран согласия в Google Auth Platform. Добавьте область `https://www.googleapis.com/auth/drive.file`. Для аудитории External в режиме Testing добавьте аккаунт, на чей Диск пойдёт загрузка, в Test users. [Настройка Drive API и OAuth](https://developers.google.com/workspace/drive/api/quickstart/python)
2. В Google Auth Platform → Clients создайте OAuth-клиент типа **Desktop app** и скачайте его JSON. Из корня Photo-tel перенесите JSON в игнорируемую папку с правами только для своего пользователя. [Создание OAuth-клиента](https://developers.google.com/workspace/guides/create-credentials#desktop-app)

   ```sh
   mkdir -m 700 -p .gdrive
   install -m 600 /путь/к/скачанному/client_secret.json .gdrive/client.json
   ```

3. Установите зависимости в Python 3.11+ и один раз подтвердите доступ в открывшемся браузере. `--authorize` сохранит токен обновления в `.gdrive/token.json` с правами `600`. Запускайте из обычного Терминала macOS, где доступна локальная петля и сеть Google.

   ```sh
   test -x .venv/bin/python || uv venv .venv
   uv pip install --python .venv/bin/python -r requirements-drive.txt
   .venv/bin/python drive_upload.py --authorize
   ```

   Для External в режиме Testing токен Google действует семь дней. Для работы после пилота настройте публикацию OAuth-приложения в Google Auth Platform и заново авторизуйте тот же аккаунт. [Срок действия refresh token](https://developers.google.com/identity/protocols/oauth2#expiration)

## Запуск и проверка

```sh
.venv/bin/python drive_upload.py
```

Первый запуск создаёт папку `Photo-tel — тестовые обои (10)` в «Моём диске». В конце программа печатает JSON с `folder_url`, `folder_id`, `verified` и `reused`. Для пилота ожидается `verified: 11`, включая 10 JPEG и CSV. Программа сверяет список, имена, MIME-типы, размер, контрольные суммы и отсутствие общего доступа. Область `drive.file` ограничивает список объектами, к которым приложению выдан доступ. [Области Google Drive](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)

После сбоя повторите ту же команду с теми же входными файлами и состоянием `.gdrive/pilot-job.json`. Скрипт сверит и пропустит завершённые загрузки. Текущий незавершённый файл начнёт загружаться с начала, с тем же ID. Если Google уже записал файл и ответ потерялся, скрипт распознает конфликт ID и сверит сохранённый объект. Второй полный запуск должен вернуть ту же ссылку и `reused: 11`. [Повтор запросов с заранее выданными ID](https://developers.google.com/workspace/drive/api/guides/create-file#generate_ids_to_use_with_your_files)

`python3 drive_upload.py --prepare` обновляет CSV локально. Для будущего приложения доступны `prepare_batch(manifest_path)`, `open_drive()` и `upload_batch(drive, files, state_path=..., folder_name=...)`. Каждой новой партии задайте свой файл состояния в `.gdrive/` через `--state` или аргумент `state_path`. Храните его между запусками вместе с входными данными партии. Для пилота используйте штатный файл состояния.
