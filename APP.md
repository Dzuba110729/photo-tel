# Photo-tel

Локальное приложение заказывает от 1 до 100 обоев у NanoGPT Seedream 5.0 Lite. В Google Диске для каждого заказа появляется отдельная приватная папка с JPEG 1870 × 4063 и файлом `prompts.csv`. В CSV каждой картинке соответствует полный текст промпта из запроса и имя модели.

## Запуск

Из папки Photo-tel в обычном Терминале macOS.

```sh
test -x .venv/bin/python || uv venv .venv
uv --no-cache pip install --python .venv/bin/python -r requirements-app.txt
.venv/bin/python -m uvicorn wallpaper_app:app --host 127.0.0.1 --port 8765
```

Откройте [Photo-tel](http://127.0.0.1:8765/). Ключ NanoGPT читается из `.nanogpt-key` или `NANOGPT_API_KEY`. Для Google Диска используется `.gdrive/token.json`. Если нужно обновить авторизацию, выполните `.venv/bin/python drive_upload.py --authorize` с тем же аккаунтом. [Подключение Google Диска](DRIVE.md)

## Заказ

1. Выберите светлые или тёмные обои для всего заказа. На макете видно, какой цвет часов подходит к выбранному тону.
2. Отметьте готовые темы или добавьте свои. Можно вставить несколько своих тем по одной на строку и нажать **Добавить**.
3. Выберите распределение. В режиме **Автоматически** введите общий тираж от 1 до 100, и кадры распределятся по темам с разницей максимум в один. В режиме **Вручную** укажите количество для каждой темы. Приложение посчитает общий тираж и стоимость. Каждая выбранная тема получает хотя бы один кадр.
4. Нажмите **Запустить заказ**. В приложении видны ответы NanoGPT, подготовленные JPEG и проверенные файлы на Диске. Ссылка появляется после проверки приватности папки. Заказ готов, когда приложение сверило состав папки и контрольные суммы всех файлов.
   
Внутри каждой темы приложение меняет сюжет, композицию, стиль, палитру и свет. Тон влияет на палитру и свет всего кадра. В светлом режиме промпт задаёт светлую спокойную зону для тёмных часов. В тёмном режиме эта зона остаётся тёмной для белых часов. Фактический тон и читаемость зависят от результата генерации.

Приложение обрабатывает заказы по очереди, до трёх кадров одновременно. Тариф, проверенный в пилоте, составляет $0,035 за кадр. Перед заказом приложение проверяет Google OAuth и наличие средств на оставшиеся запросы. При том же тарифе 100 кадров обойдутся примерно в $3,50.

## Возобновление

Состояние всех заказов хранится в `orders-data/orders.sqlite3`. В папке заказа под `orders-data/` остаются исходники, JPEG, манифест с замерами, CSV и назначенные ID объектов Google Диска. Храните каталог целиком между запусками. Закрытие вкладки не прерывает заказ. После перезапуска сервера заказы из очереди и незавершённые активные заказы продолжаются автоматически.

При однозначном отказе NanoGPT или сбое скачивания либо выгрузки нажмите **Возобновить** после устранения причины. Приложение использует сохранённые ответы, исходники, готовые JPEG и ID Диска. Если результат платного запроса неизвестен, заказ ждёт ручной сверки в NanoGPT Usage. Приложение блокирует повторную отправку такого кадра. Если ссылка из сохранённого ответа успела истечь до первого скачивания, для восстановления потребуется оригинал от провайдера.

Команда запуска рассчитана на один локальный процесс. Ссылки Google Диска открываются в аккаунте владельца папки.

## Логотип

Знак Photo-tel создан встроенным ImageGen. [Прозрачный PNG](assets/photo-tel-logo.png) используется в шапке и как иконка вкладки.

<details>
<summary>Промпт для логотипа</summary>

```text
Use case: logo-brand. Create one original production-ready transparent PNG logomark for Photo-tel, a refined studio that makes bespoke smartphone wallpapers. Draw a single sophisticated abstract geometric monogram in flat warm brushed-copper color (#cf8c60 as visual reference), designed like precision industrial engraving: a compact vertical architectural aperture with an offset horizon cut and negative space suggesting the letter P and an image frame in one shape. One strong silhouette, few continuous shapes, softly chamfered ends, superb optical balance, vector-like clean edges and legible at 24 pixels. Graphic only, no letters or words, no caption, no border around the image, no mock-up, no shadow or lighting or metallic gradient, no extra objects. Uniform genuinely transparent background around and within the mark. Center it with modest even padding in a square canvas. The symbol should feel distinctive in its own right and will be paired with the separate Photo-tel wordmark.
```

</details>

## Состояние перед заказом на 100

Отдельный тест на 100 выполнен с имитацией NanoGPT и Google Диска. Замеры реального заказа приведены в [README](README.md#проверка-на-реальных-заказах).
