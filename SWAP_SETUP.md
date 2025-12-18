# Инструкция: включение swap-файла на Ubuntu

## Зачем
Swap — запас для ОЗУ. Помогает избежать OOM при больших задачах. Минус: при активном свопе операции медленнее (диск).

## Что будет сделано
- Создадим файл `/swapfile` (пример — 2 ГБ, можно выбрать другой размер).
- Выставим права 600 (только root).
- Разметим как swap, подключим.
- Добавим строку в `/etc/fstab` для автоподключения.

## Шаги
1) Проверить свободное место (swap — это место на диске):
```
df -h /
```

2) Создать файл (пример 2 ГБ, измените 2G при необходимости):
```
sudo fallocate -l 2G /swapfile
```
Если fallocate недоступен, можно через dd:
```
sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
```

3) Закрыть права (только root):
```
sudo chmod 600 /swapfile
```

4) Разметить как swap и подключить:
```
sudo mkswap /swapfile
sudo swapon /swapfile
```

5) Проверить, что swap активен:
```
sudo swapon --show
free -h
```
Должна появиться строка Swap с размером.

6) Автоподключение после перезагрузки — добавить строку в конец `/etc/fstab`:
```
/swapfile none swap sw 0 0
```
Удобно отредактировать так:
```
sudo nano /etc/fstab
```
Сохранить (Ctrl+O, Enter) и выйти (Ctrl+X), затем проверить корректность:
```
sudo mount -a
sudo swapon --show
```

## Как удалить swap-файл
Если больше не нужен:
```
sudo swapoff /swapfile
sudo rm /swapfile
```
И удалите строку `/swapfile none swap sw 0 0` из `/etc/fstab`, после чего:
```
sudo mount -a
```

## Настройка swappiness
`swappiness` — насколько охотно ядро уводит данные в swap (0 — почти не свопить, 100 — свопить активно). Для ВМ с 2 ГБ RAM разумно 20.

Проверить текущее значение:
```
cat /proc/sys/vm/swappiness
```

Поставить временно (до перезагрузки) значение 20:
```
sudo sysctl vm.swappiness=20
```

Постоянно (через `/etc/sysctl.conf`):
```
echo 'vm.swappiness=20' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

При swappiness=20 система меньше охотно свопит. OOM всё равно возможен, если RAM+swap реально заканчиваются; это настройка баланса, не защита от полной нехватки памяти.

