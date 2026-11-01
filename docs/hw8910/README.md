# Исходный прототип ДЗ 8–10

Это неисправленная учебная заготовка из `mlops26-hw8-9-10/hw8910-broken`.
`lab.py`, `release.py`, `service.py`, `defects.csv`, примеры JSON и 13 приложенных
тестов перенесены побайтно. Реализации содержат исходные дефекты.

Команды выполняются из корня репозитория на Python 3.12 без дополнительных пакетов
и весов. Рабочие каталоги создавайте вне репозитория, чтобы не добавлять фикстуры
и кеш в Git. Каждый независимый опыт требует нового пути workspace:

```sh
python3 lab.py --workspace /tmp/mlops-hw8910-first init
python3 lab.py --workspace /tmp/mlops-hw8910-first run --fail-after-prepare
python3 lab.py --workspace /tmp/mlops-hw8910-first run
python3 service.py --workspace /tmp/mlops-hw8910-first --port 8091 --warmup-seconds 10
```

Исходные команды `python3 lab.py init`, `python3 lab.py run` и
`python3 service.py --port 8091 --warmup-seconds 10` также совместимы: без
`--workspace` они используют `.lab` в текущем каталоге. Не добавляйте `.lab` в Git.
Сбой `--fail-after-prepare` намеренный. `STUB ONLY` не является оценкой или ответом
настоящей модели.

Для приложенных тестов выделен отдельный каталог `tests_hw8910/`:

```sh
python3 -m unittest discover -s tests_hw8910 -v
make check-hw8910
```

Это адаптация исходной команды `python3 -m unittest discover -s tests -v`:
`tests/` проекта остаётся каталогом ДЗ 6–7 и предыдущих работ. Новые регрессионные
тесты ДЗ 8–10 также следует размещать в `tests_hw8910/`, отдельными файлами,
не меняя приложенный `test_release.py`. На исходной версии проверки ожидаемо
падают; зелёный прогон не является результатом этого этапа.

Учебные `README.txt`, `CONTRACTS.txt`, `INCIDENTS.txt` и `REPORT.txt` сохранены рядом
без изменений. `Makefile` и `ci.example.yml` здесь — оригинальные образцы,
предполагающие `tests/` в корне; рабочая адаптированная команда находится в корневом
Makefile. Шаблон CI не активирован и не доказывает запуск удалённых проверок.
Синтетические примеры находятся в `examples/hw8910/`, таблица расследования —
`defects.csv` в корне. На этом этапе причины и исправления в неё не добавлялись.
