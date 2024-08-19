# Tion Home Assistant
Интеграция обеспечивает управление бризерами Tion, а также чтение показаний датчиков (включая датчики MagicAir) из системы умного дома Home Assistant.

*Внимание: для работы требуется шлюз MagicAir!*

## Установка

### HACS:
1. HACS->Settings->Custom repositories 
2. Добавьте `vaproloff/tion_home_assistant` в поле `ADD CUSTOM REPOSITORY` и выберите `Integration` в `CATEGORY`. Щелкните кнопку `Save`
3. перезагрузите Home Assistant

### Без HACS:
1. скачайте zip файл с компонентом
2. поместите содержимое в `config/custom_components/tion` папку системы Home Assistant
3. перезагрузите Home Assistant

## Настройка
> Настройки > Интеграции > Добавить интеграцию > **Tion**

Если интеграции нет в списке - очистите кэш браузера.

## Использование:
Среди устройств должны появиться бризеры `climate.tion_...` и датчики MagicAir `sensor.magicair_..`.

Службы Home Assistant для управления вашими устройствами:
### climate.set_fan_mode
`fan_mode` задает скорость бризера следующим образом (тип - строка):
- `1`-`6` - включить в ручном режиме с заданной скоростью
- `auto` - автоматическое управление скоростью в зависимости от уровня CO2

### climate.set_hvac_mode
`hvac_mode` задает режим работы прибора:
- `heat` - нагреватель включен
- `fan_only` - нагреватель выключен
- `off` - прибор выключен

### climate.set_temperature
Используйте для задачи целевой температуры нагревателя

### tion.set_zone_target_co2
Используйте для задачи целевого уровня CO2 для (в Авто режиме бризера)

### tion.set_breezer_min_speed
Используйте для задачи минимальной скорости (в Авто режиме бризера)

### tion.set_breezer_max_speed
Используйте для задачи максимальной скорости (в Авто режиме бризера)

## Если что-то не работает
Включите расширенное логирование для интеграции и пакета `tion` в файле конфигурации `configuration.yaml`:
```yaml
logger:
  default: warning
  logs:
    custom_components.tion: info
    tion: info
```
