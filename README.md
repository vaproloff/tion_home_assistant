# Tion Home Assistant

[![HACS validation](https://github.com/vaproloff/tion_home_assistant/actions/workflows/hacs.yaml/badge.svg)](https://github.com/vaproloff/tion_home_assistant/actions/workflows/hacs.yaml)
[![Hassfest](https://github.com/vaproloff/tion_home_assistant/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/vaproloff/tion_home_assistant/actions/workflows/hassfest.yaml)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-blue)
![Version](https://img.shields.io/badge/version-2026.5.0-blue)
![HACS](https://img.shields.io/badge/HACS-Custom-orange)
[![GitHub stars](https://img.shields.io/github/stars/vaproloff/tion_home_assistant?style=social)](https://github.com/vaproloff/tion_home_assistant/stargazers)

Интеграция добавляет полноценное управление устройствами Tion в HomeAssistant.

> Для работы нужен аккаунт Tion и станция/шлюз MagicAir, через которую устройства доступны в облаке Tion.

> Если понравилась интеграция - буду рад вашей отметке ⭐️ на Github!

![Tion integration screenshot](docs/screenshot.png)

## English summary

This is a custom HomeAssistant integration for Tion Breezer 3S/4S, MagicAir and Module CO2+ devices. It uses the Tion/MagicAir cloud API, exposes native HomeAssistant entities, and supports setup through the UI.

## Что умеет интеграция

- Управлять бризерами прямо из HomeAssistant: включать, выключать, выбирать скорость и температуру подогрева.
- Переключать забор воздуха с улицы, из комнаты или смешанный режим, если это поддерживает модель.
- Показывать температуру входящего и исходящего потока воздуха.
- Следить за CO2, влажностью, температурой и PM2.5 с MagicAir или Module CO2+.
- Настраивать целевой CO2 и границы скорости для автоматического режима.
- Управлять подогревом, подсветкой и звуковыми сигналами устройств.
- Напоминать о замене фильтров и сбрасывать счётчик после обслуживания (только для 3S/4S).

## Поддерживаемые устройства

- Tion Breezer 3S
- Tion Breezer 4S
- Tion MagicAir
- Tion Module CO2+

## Установка

### HACS

Рекомендуемый способ установки - через HACS. Так HomeAssistant будет видеть обновления интеграции обычным способом.

1. Откройте `HACS` -> `...` -> `Пользовательские репозитории`.
2. Добавьте репозиторий `https://github.com/vaproloff/tion_home_assistant`.
3. Выберите тип `Интеграция` и нажмите `Добавить`.
4. Найдите `Tion` в HACS и установите интеграцию.
5. Перезагрузите HomeAssistant.

### Вручную

1. Скачайте архив репозитория.
2. Скопируйте каталог `custom_components/tion` в `config/custom_components/tion`.
3. Перезагрузите HomeAssistant.

## Настройка

1. Откройте `Настройки` -> `Устройства и службы`.
2. Нажмите `Добавить интеграцию`.
3. Найдите `Tion`.
4. Введите логин и пароль от аккаунта Tion.

После подключения интеграция сама найдёт доступные локации, зоны и устройства в аккаунте Tion. Если пароль изменится или токен перестанет работать, HomeAssistant попросит пройти повторную авторизацию.

## Что появится в HomeAssistant

| Что | Сущности | Для чего |
| --- | --- | --- |
| Бризер | `climate` | Включение, выключение, нагрев, температура, скорость и источник воздуха |
| Качество воздуха | `sensor` | Температура, влажность, CO2 и PM2.5 (если датчик доступен) |
| Потоки воздуха | `sensor` | Температура входящего и исходящего потока бризера |
| Обслуживание фильтров | `sensor`, `binary_sensor`, `button` | Срок замены, сигнал о необходимости замены и сброс счётчика |
| Настройки устройства | `switch`, `number` | Подогрев, подсветка, звук, целевой CO2 и пределы скорости Авто режима |

Интеграция использует стандартные действия HomeAssistant, поэтому её можно подключать к автоматизациям, скриптам, дашбордам и голосовым сценариям без отдельных custom-сервисов.

## Если что-то не работает

Сначала проверьте простые вещи:

1. Устройства видны в официальном приложении Tion.
2. Станция MagicAir подключена к аккаунту Tion и находится онлайн.
3. После установки HomeAssistant был перезагружен.
4. Если интеграция не появилась в поиске, попробуйте очистить кэш браузера.

Для диагностики можно включить debug-логирование:

```yaml
logger:
  default: info
  logs:
    custom_components.tion: debug
```

Если проблема повторяется, создайте issue и приложите описание ситуации вместе с логами: https://github.com/vaproloff/tion_home_assistant/issues/new

## Техническая информация

Этот проект - custom integration для HomeAssistant с полноценной UI-настройкой, несколькими платформами сущностей и адаптацией облачного API Tion под модель Home Assistant.

Технически внутри есть несколько важных частей:

- `config_flow` для настройки через интерфейс, проверки логина и пароля, повторной авторизации и опций обновления.
- `DataUpdateCoordinator` с cloud polling через Tion/MagicAir API, общим состоянием локаций, зон и устройств.
- Нативные платформы Home Assistant: `climate`, `sensor`, `binary_sensor`, `button`, `number`, `switch`.
- Отправка команд в API Tion через очередь задач: интеграция ждёт завершения команды, обновляет состояние и защищается от устаревших данных, которые могли прийти параллельно.
- Учёт различий между Breezer 3S и Breezer 4S: режимы заслонки, поведение нагревателя и доступные возможности устройства.
- Локализация интерфейса на русском и английском, HACS metadata, Hassfest и HACS validation в GitHub Actions.

Интеграция работает через облако Tion (`cloud_polling`). Локальное управление устройствами напрямую не заявлено и не требуется для установки.
