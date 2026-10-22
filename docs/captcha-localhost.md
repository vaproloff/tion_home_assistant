# Вход в Tion через localhost

Эта страница объясняет, как войти в Tion из Home Assistant, когда Tion принимает капчу только со страницы по адресу `http://localhost`. В конце страницы есть краткое резюме на английском (English summary).

## Что случилось и кого это касается

С октября 2026 сервер Tion проверяет капчу (Yandex SmartCaptcha) и принимает её, только если страница с капчей открыта по адресу ровно `http://localhost` (без порта). Страница, открытая по адресу Home Assistant (`http://homeassistant.local:8123`, облачный домен и так далее), проверку не проходит, и интеграция показывает ошибку «Tion не принял капчу».

Это касается только входа: новой установки интеграции и повторной авторизации (пароль, капча, код из письма).

**Уже настроенная интеграция продолжает работать. Не удаляйте запись интеграции**: после удаления сущности получат новые `entity_id`, а автоматизации и дашборды перестанут на них ссылаться. Если Home Assistant показывает уведомление «нужна повторная авторизация», а устройства при этом работают, сначала перезагрузите запись: `Настройки` -> `Устройства и службы` -> Tion -> три точки -> `Перезагрузить`. Это снимает ложный запрос, который появляется при сбоях серверов Tion. Проброс порта нужен только тогда, когда вход действительно требуется.

## Что понадобится

Проброс порта нужен только на время входа (несколько минут), потом его можно выключить.

- Компьютер с браузером (Windows, macOS или Linux), с которого вы входите в Home Assistant. С телефона и планшета этот способ не работает.
- На этом компьютере должен быть свободен порт 80. На Linux для этого нужен `sudo`, на macOS и Windows права администратора для порта 80 не нужны (для `netsh` в Windows нужна консоль от имени администратора).
- Home Assistant доступен с этого компьютера одним из двух способов. От него зависит рецепт:
  - **по обычному `http` в локальной сети**, например `http://homeassistant.local:8123` или `http://192.168.1.50:8123`. Это раздел «Home Assistant дома, по http»;
  - **по своему HTTPS-адресу**: облачный домен Keenetic, Nabu Casa, собственный домен за прокси. Это раздел «Home Assistant по HTTPS-адресу». Туда же относится случай, когда TLS включён на самом Home Assistant.

Если вы не уверены, выберите по адресу в строке браузера, когда открываете Home Assistant: `http://...` или `https://...`.

Во всех командах ниже угловые скобки обозначают то, что нужно подставить: `<HA>` это адрес или IP-адрес Home Assistant в вашей сети (например `192.168.1.50`), `<IP HA>` его IP-адрес, `<домен>` ваш HTTPS-адрес без `https://`, `<пользователь>` имя пользователя для SSH на Home Assistant. Все рецепты слушают только `127.0.0.1` (кроме двух готовых альтернатив для HTTPS, о них сказано отдельно), поэтому Home Assistant не открывается наружу.

## Home Assistant дома, по http

Выберите один рецепт. Каждый включает проброс `127.0.0.1:80` на Home Assistant (порт `8123`), после включения переходите к разделу «Проверка».

### Windows: встроенный `netsh`

Откройте PowerShell или «Командную строку» от имени администратора и выполните:

```
netsh interface portproxy add v4tov4 listenport=80 listenaddress=127.0.0.1 connectport=8123 connectaddress=<IP HA>
```

Выключить проброс после входа:

```
netsh interface portproxy delete v4tov4 listenport=80 listenaddress=127.0.0.1
```

Правило `netsh` остаётся в Windows и после перезагрузки, поэтому обязательно удалите его командой выше. Если порт 80 уже занят (IIS, Skype, другой веб-сервер), `http://localhost/` откроет не Home Assistant: освободите порт или остановите этот сервис на время входа.

### macOS и Linux: SSH на Home Assistant

Подходит, если на Home Assistant есть SSH (например аддон Terminal & SSH):

```
ssh -L 127.0.0.1:80:localhost:8123 <пользователь>@<HA>
```

На Linux перед командой нужен `sudo`: `sudo ssh -L 127.0.0.1:80:localhost:8123 <пользователь>@<HA>`. Под `sudo` SSH ищет ключи в домашней папке root, поэтому при входе по ключу добавьте `-i ~/.ssh/<файл ключа>`. На macOS `sudo` не нужен.

Проброс работает, пока открыт этот сеанс SSH. Чтобы выключить его, завершите сеанс (`exit` или Ctrl+C).

### macOS и Linux: `socat`

Подходит, если SSH на Home Assistant нет. Установите `socat` (`brew install socat` на macOS, `sudo apt install socat` в Debian и Ubuntu) и выполните:

```
socat TCP-LISTEN:80,bind=127.0.0.1,fork,reuseaddr TCP:<HA>:8123
```

На Linux перед командой нужен `sudo`. Выключить проброс: Ctrl+C в этом окне.

### Любая ОС с Python 3: скрипт `captcha_proxy_local.py`

Скачайте скрипт (он лежит в этой же папке репозитория) и запустите. Нужен только Python 3.9 или новее, больше ничего устанавливать не надо.

```
curl -O https://raw.githubusercontent.com/vaproloff/tion_home_assistant/master/docs/captcha_proxy_local.py
python3 captcha_proxy_local.py <HA> 8123
```

В Windows команда `curl` в PowerShell это псевдоним другой команды: пишите `curl.exe -O ...`, а Python запускайте как `python` или `py`. На Linux перед `python3` нужен `sudo`. Скрипт пишет в окно, что делает; выключить его можно Ctrl+C. Если порт 80 занят или не хватает прав, скрипт так и скажет.

### Docker

Если на компьютере есть Docker:

```
docker run --rm -p 127.0.0.1:80:8123 alpine/socat TCP-LISTEN:8123,fork,reuseaddr TCP:<HA>:8123
```

Порт публикуется только на `127.0.0.1`. Выключить: Ctrl+C в этом окне (контейнер удалится сам из-за `--rm`).

## Home Assistant по HTTPS-адресу

Если Home Assistant открывается по `https://<домен>`, обычный проброс TCP-порта (`netsh`, `ssh -L`, `socat`, Docker) здесь не годится. Браузер открывает `http://localhost` без шифрования, а адрес за HTTPS ждёт TLS и запросы со своим именем хоста. Нужен HTTP-прокси: он принимает запрос браузера и в запросе к вашему Home Assistant подставляет имя хоста Home Assistant (заголовок `Host`). Это касается только связи с вашим Home Assistant; Tion прокси не затрагивает: капча и вход идут из вашего браузера со страницы `http://localhost`.

### Скрипт `captcha_proxy_https.py`

Скачайте скрипт и запустите (Python 3.9 или новее, без установки пакетов):

```
curl -O https://raw.githubusercontent.com/vaproloff/tion_home_assistant/master/docs/captcha_proxy_https.py
python3 captcha_proxy_https.py https://<домен>
```

Если адрес с портом, укажите его: `https://<домен>:<порт>`. Допустим и `http://<хост>:<порт>` (например, Home Assistant с http, но за обратным прокси с нестандартным именем хоста). Особенности запуска те же, что у `captcha_proxy_local.py`: `curl.exe` и `py` в Windows, `sudo` на Linux, выключение Ctrl+C. Если у адреса самоподписанный сертификат, скрипт ответит ошибкой 502 «certificate verify failed»: используйте адрес с доверенным сертификатом.

### Готовые альтернативы

Если у вас уже есть `mitmproxy` или Caddy, можно обойтись без скрипта:

```
mitmdump --mode reverse:https://<домен> -p 80
```

```
caddy reverse-proxy --from :80 --to https://<домен> --change-host-header
```

Обе команды по умолчанию слушают все сетевые интерфейсы компьютера, а не только `127.0.0.1`: пока они работают, Home Assistant на этом порту доступен всей вашей локальной сети. Для `mitmdump` добавьте `--listen-host 127.0.0.1`; Caddy остановите (Ctrl+C) сразу после входа. Если сомневаетесь, используйте наш скрипт: он слушает только `127.0.0.1`.

## Home Assistant и браузер на одном компьютере

Если Home Assistant запущен на том же компьютере, где открыт браузер (Home Assistant Core в виртуальном окружении или Docker Desktop), отдельный прокси обычно не нужен:

- **Docker (в том числе Docker Desktop):** опубликуйте порт контейнера Home Assistant как порт 80 компьютера: `-p 127.0.0.1:80:8123` в `docker run` или `"127.0.0.1:80:8123"` в `ports:` в `docker-compose.yml`.
- **Home Assistant Core в виртуальном окружении:** временно запустите Home Assistant на порту 80 (в `configuration.yaml`, раздел `http:`, параметр `server_port: 80`; потом верните `8123`) либо запустите `python3 captcha_proxy_local.py 127.0.0.1 8123`.

## Проверка

Когда проброс включён, откройте в браузере `http://localhost/`. Должна появиться страница входа Home Assistant (или сам Home Assistant, если вы уже входили в него). Если страница не открывается, проброс не работает: проверьте, что команда ещё запущена и не выдала ошибку, что порт 80 свободен и что `<HA>` или `<IP HA>` указан верно (его должен открывать и сам браузер: `http://<HA>:8123`).

## Вход

1. Не выключая проброс, добавьте интеграцию Tion (`Настройки` -> `Устройства и службы` -> `Добавить интеграцию` -> Tion) или откройте повторную авторизацию. Home Assistant можно открывать по его обычному адресу, не по `localhost`.
2. Введите почту и пароль, а в поле «Адрес страницы капчи» укажите `http://localhost` (без порта и пути).
3. Нажмите «Отправить». Страница с капчей откроется в новом окне уже по адресу `http://localhost/...`. Пройдите проверку, если она появится. Страница напишет «Готово, вернитесь в Home Assistant» и закроется сама, а Home Assistant перейдёт к вводу кода из письма.
4. Введите код из письма. Вход выполнен.
5. Выключите проброс (команда выключения указана в рецепте, либо Ctrl+C). Он нужен только на время капчи. Home Assistant сам продлевает сессию Tion, поэтому повторный вход потребуется, лишь если Tion окончательно отклонит обновление сессии.

Адрес запоминается в записи интеграции, и при повторной авторизации поле уже заполнено им. Проброс при этом всё равно нужно включить заново.

## Ограничения

- Через HTTPS-адрес работает только HTTP-прокси: `captcha_proxy_https.py`, `mitmdump` или Caddy со сменой заголовка `Host`. TCP-проброс и `ssh -L` там не годятся. То же относится к случаю, когда TLS включён на самом Home Assistant.
- Скрипты рассчитаны на страницу капчи, а не на работу с Home Assistant целиком: у них нет keep-alive, WebSocket-соединения (живые обновления интерфейса) через прокси не работают. Для входа этого достаточно.
- С телефона способ не работает: на нём `localhost` означает сам телефон.

## Почему так

Приложение Tion само живёт на `localhost`, и сервер Tion проверяет, с какого хоста открыта страница с капчей. Браузер сообщает этот хост честно, и мы ничего не подменяем: страница капчи действительно открывается по адресу `http://localhost`. Слово `localhost` всегда означает компьютер, на котором работает браузер, а не Home Assistant. Поэтому проброс порта нужен на вашем компьютере: он превращает `http://localhost/` в окно в ваш Home Assistant. Это обходной путь под серверную проверку Tion, а не настройка Home Assistant.

## English summary

- Since October 2026 Tion's server accepts the captcha token only from a page whose address is exactly `http://localhost`. Only new setups and re-authentication are affected.
- An already configured integration keeps working: do not delete it. If you get a re-authentication notice while devices work, reload the integration entry first.
- `localhost` always means the computer the browser runs on, so forward port 80 of your own computer (`127.0.0.1:80`) to Home Assistant (port 8123) for the few minutes of the login.
- Options: `netsh interface portproxy` (Windows), `ssh -L` or `socat` (macOS/Linux), Docker with `alpine/socat`, or the scripts `captcha_proxy_local.py` (plain TCP, Home Assistant over http) and `captcha_proxy_https.py` (HTTP proxy for Home Assistant behind an HTTPS address, where a TCP forward does not work).
- Check that `http://localhost/` shows the Home Assistant login page, then enter `http://localhost` in the "Адрес страницы капчи" (captcha page address) field of the login form.
- Nothing is spoofed: the captcha page really is opened at `http://localhost`. Remove the forwarding after you have signed in.
