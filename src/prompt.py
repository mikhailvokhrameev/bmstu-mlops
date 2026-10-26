"""Сборка текста диалога.

Модуль общий для обучения и инференса.
"""

import re
from typing import Any


def _template_kwargs(params: dict) -> dict:
    """Доп. аргументы шаблона, которые есть не у всех моделей.

    `enable_thinking` понимает только семейство с режимом рассуждений (Qwen3; SmolLM2 его не имеет);
    остальные шаблоны молча его игнорируют, поэтому передаём только если задан.
    """
    value = params["model"].get("enable_thinking")
    return {} if value is None else {"enable_thinking": value}


def split_messages(messages: list[dict]) -> tuple[list[dict], dict]:
    """Разделить диалог на промпт и ответ ассистента.

    Промпт — всё, что модель видит на входе. Ответ — последняя реплика
    ассистента, ровно она и должна попасть в лосс.
    """
    if not messages or messages[-1]["role"] != "assistant":
        raise ValueError("последняя реплика диалога обязана быть ответом ассистента")
    return messages[:-1], messages[-1]


def render_chat_template(
    tokenizer: Any,
    messages: list[dict],
    add_generation_prompt: bool,
    **kwargs: Any,
) -> str:
    """Единственное место в проекте, где вызывается apply_chat_template.

    Никогда не склеивайте роли вручную: у каждой модели свой формат,
    а расхождение шаблонов обучения и инференса — самая частая тихая ошибка.
    """
    return tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=add_generation_prompt, **kwargs
    )


def build_chat_text(
    tokenizer: Any,
    messages: list[dict],
    params: dict,
    add_generation_prompt: bool,
) -> str:
    """Собрать текст диалога шаблоном модели.

    `add_generation_prompt=True`  — путь инференса: только промпт, строка
        заканчивается заголовком ответа ассистента; последняя реплика
        ассистента (если она есть в `messages`) отбрасывается.
    `add_generation_prompt=False` — путь обучения: весь диалог вместе
        с ответом и eos.
    """
    if add_generation_prompt:
        messages, _ = split_messages(messages)
    return render_chat_template(
        tokenizer, messages, add_generation_prompt, **_template_kwargs(params)
    )


def encode_chat_text(
    tokenizer: Any, text: str
) -> tuple[list[int], list[tuple[int, int]]]:
    """Токенизировать текст диалога так, чтобы содержимое реплик не вносило спецтокены.

    Управляющие токены шаблона (вида `<|...|>`: начало и конец реплики) остаются
    настоящими спецтокенами, а всё между ними кодируется как обычный текст
    (`split_special_tokens=True`). Иначе литерал `<repo_name>` или `<filename>`
    в ответе превращается в служебный токен SmolLM2: в обучение попадает
    не то, что написал автор, а после decode(skip_special_tokens=True) он пропадает.
    """
    control = {
        t for t in tokenizer.all_special_tokens
        if t.startswith("<|") and t.endswith("|>")
    }
    ids: list[int] = []
    offsets: list[tuple[int, int]] = []
    pattern = "(" + "|".join(re.escape(t) for t in sorted(control, key=len, reverse=True)) + ")"
    pos = 0
    for part in (re.split(pattern, text) if control else [text]):
        if not part:
            continue
        if part in control:
            ids.append(tokenizer.convert_tokens_to_ids(part))
            offsets.append((pos, pos + len(part)))
        else:
            enc = tokenizer(
                part, add_special_tokens=False, split_special_tokens=True,
                return_offsets_mapping=True,
            )
            ids.extend(enc["input_ids"])
            offsets.extend((pos + a, pos + b) for a, b in enc["offset_mapping"])
        pos += len(part)
    return ids, offsets


def prompt_token_len(
    tokenizer: Any,
    prompt_text: str,
    full_ids: list[int],
    full_offsets: list[tuple[int, int]],
) -> tuple[int, bool]:
    """Длина промпта в токенах — граница маскирования лосса.

    Возвращает `(число токенов промпта, сработал ли запасной путь)`.

    Основной путь: токенизировать промпт отдельно и убедиться, что его токены
    совпадают с началом токенов полного текста. Обычно так и есть.

    Запасной путь нужен из-за BPE: токенизатор режет не по символам, а по
    статистике, и токен может «склеить» конец промпта с началом ответа —
    тогда пары одинаковых токенов на границе нет. Тогда берём границу по
    символам: первый токен, который НАЧИНАЕТСЯ не раньше конца промпта.
    Склеенный токен при этом уходит в промпт (маскируется). Это осознанный
    выбор: потерять первый символ ответа из лосса безопаснее, чем учить
    модель дописывать хвост промпта. Доля таких примеров пишется в метрики.
    """
    prompt_ids, _ = encode_chat_text(tokenizer, prompt_text)
    n = len(prompt_ids)
    if full_ids[:n] == prompt_ids:
        return n, False

    boundary = len(prompt_text)
    for i, (start, _end) in enumerate(full_offsets):
        if start >= boundary:
            return i, True
    raise ValueError(
        "не нашли начало ответа ассистента: за границей промпта не осталось "
        "ни одного токена (текст оборван либо ответ целиком склеился с промптом)"
    )
