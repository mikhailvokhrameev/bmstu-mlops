"""Стадия train: LoRA-дообучение на выходе стадии tokenize.

    python -m src.train --variant all_layers          # полный прогон варианта
    python -m src.train --variant all_layers --max-steps 4 --out /tmp/x   # smoke

Цикл обучения написан руками, а не через Trainer: так видно всё, что
обычно прячется, — где считается val loss, как копятся градиенты, что
сохраняется рядом с адаптером.

Что сохраняется в models/adapter_<variant>/: адаптер и токенайзер.
В metrics/train_<variant>.json — кривые train/val loss, время, пиковая память,
число обучаемых параметров, вес адаптера и отпечаток входов.
"""

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path

# Потолок памяти Metal — до импорта torch. Без него mps занимает сколько дадут,
# и система уходит в своп вместо внятной ошибки. На 8 ГБ 0.5 (~2,7 ГиБ от рабочего
# набора Metal) мало для модели + логитов на 1536 токенов, 0.8 и выше уже свопит: ставим 0.7.
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.7")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.6")   # нижний порог не выше верхнего

import torch  # noqa: E402
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup

from src.config import load_params
from src.data import LABEL_PAD_ID, batches, load_split
from src.runtime import allocated_bytes, memory_metric, resolve_device, resolve_dtype, set_seed


TRAIN_CODE = ("src/train.py", "src/data.py", "src/runtime.py", "src/config.py")
TRAIN_PARAMS = ("model", "data", "lora_train", "train", "variants")


def inputs_fingerprint(params: dict) -> str:
    """Отпечаток кода обучения и секций конфига, от которых зависит адаптер.

    check.sh сверяет его с текущим: правили train.py или lr, а адаптер
    остался от прошлого прогона — проверять его бессмысленно.
    """
    h = hashlib.sha256()
    for name in TRAIN_CODE:
        h.update(name.encode())
        h.update(Path(name).read_bytes())
    h.update(json.dumps({k: params.get(k) for k in TRAIN_PARAMS}, sort_keys=True).encode())
    return h.hexdigest()[:12]


def lora_config(params: dict, n_layers: int, freeze_first: int) -> LoraConfig:
    cfg = params["lora_train"]
    return LoraConfig(
        r=cfg["r"],
        lora_alpha=cfg["alpha"],
        lora_dropout=cfg["dropout"],
        target_modules=cfg["target_modules"],
        modules_to_save=cfg.get("modules_to_save"),
        # Без этого LoRA ставится на все слои, и freeze_first ни на что не влияет.
        layers_to_transform=list(range(freeze_first, n_layers)) if freeze_first > 0 else None,
        task_type="CAUSAL_LM",
    )


LOSS_CHUNK = 256   # позиций за один lm_head: 256 x 151 936 x 4 байта = 155 МБ логитов в float32


def _chunk_nll(hidden: torch.Tensor, weight: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """Сумма NLL по куску позиций. Логиты живут только внутри этой функции."""
    logits = F.linear(hidden, weight).float()
    return F.cross_entropy(logits, target, ignore_index=LABEL_PAD_ID, reduction="sum")


def masked_loss(model, batch: dict) -> torch.Tensor:
    """Средний лосс на токен без логитов на весь словарь для всей последовательности.

    model(**batch).loss строит логиты для каждого токена, в том числе для промпта
    (labels = -100): на 1536 токенах это ~1 ГБ в float32 плюс копии под log_softmax
    и градиент, и именно здесь падает MPS OOM. Здесь lm_head считается
    только на позициях с метками и кусками по LOSS_CHUNK; каждый кусок обёрнут
    в checkpoint, так что на backward его логиты пересчитываются, а не хранятся.
    Значение то же, что у model(**batch).loss: cross_entropy и так игнорирует -100.
    """
    labels = batch["labels"]
    inner = model.get_base_model()   # Qwen3ForCausalLM с уже вставленными LoRA-слоями
    hidden = inner.model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).last_hidden_state
    idx = (labels[:, 1:] != LABEL_PAD_ID).any(dim=0).nonzero().squeeze(-1)   # позиции, чей следующий токен размечен
    hidden = hidden[:, idx].flatten(0, 1)
    target = labels[:, idx + 1].flatten()
    weight = inner.lm_head.weight
    total = hidden.new_zeros((), dtype=torch.float32)
    for i in range(0, target.numel(), LOSS_CHUNK):
        total = total + checkpoint(_chunk_nll, hidden[i:i + LOSS_CHUNK], weight, target[i:i + LOSS_CHUNK],
                                   use_reentrant=False)
    return total / (target != LABEL_PAD_ID).sum().clamp(min=1)


@torch.no_grad()
def evaluate(model, examples, pad_id, device, batch_size: int) -> float:
    """Средний лосс на токен по всему val-сплиту.

    Среднее по батчам нельзя: в батчах разное число токенов под маской.
    Поэтому сумма лоссов, взвешенная числом токенов, делённая на их сумму.
    """
    model.eval()
    total, count = 0.0, 0
    for batch in batches(examples, batch_size, pad_id, shuffle=False, seed=0):
        batch = {k: v.to(device) for k, v in batch.items()}
        n = int((batch["labels"][:, 1:] != LABEL_PAD_ID).sum())
        if n == 0:
            continue
        loss = masked_loss(model, batch)
        total += loss.item() * n
        count += n
    model.train()
    if device.type == "mps":
        torch.mps.empty_cache()   # логиты оценки не должны висеть в кэше до конца обучения
    return total / max(count, 1)


def dir_size_mb(path: Path) -> float:
    return round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1048576, 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="all_layers")
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--out", default=None, help="куда писать адаптер и метрики (smoke-тесты)")
    ap.add_argument("--val-limit", type=int, default=None, help="оценивать на первых N примерах val (smoke)")
    args = ap.parse_args()

    params = load_params()
    variants = {v["name"]: v for v in params["variants"]}
    if args.variant not in variants:
        raise SystemExit(f"нет варианта {args.variant!r}, есть: {sorted(variants)}")
    variant = variants[args.variant]
    tcfg = params["train"]
    max_steps = args.max_steps if args.max_steps is not None else tcfg.get("max_steps")

    device = resolve_device(params["model"]["device"])
    dtype = resolve_dtype(params["model"]["dtype"])

    train_blob = load_split(params["data"]["train"])
    val_blob = load_split(params["data"]["val"])
    max_len = tcfg.get("max_example_tokens")
    if max_len:
        # На 8 ГБ длинные примеры упираются в потолок Metal (OOM, затем NaN). Фильтр только
        # здесь: токенизированные файлы ДЗ 4 и их проверки остаются как есть.
        for name, blob in (("train", train_blob), ("val", val_blob)):
            before = len(blob["examples"])
            blob["examples"] = [e for e in blob["examples"] if len(e["input_ids"]) <= max_len]
            print(f"  {name}: {len(blob['examples'])} из {before} примеров не длиннее {max_len} токенов")
    if args.val_limit:
        val_blob["examples"] = val_blob["examples"][:args.val_limit]
    pad_id = train_blob["pad_token_id"]

    set_seed(tcfg["seed"])   # до загрузки модели и get_peft_model: от него зависят init LoRA A и dropout
    tokenizer = AutoTokenizer.from_pretrained(params["model"]["name"])
    model = AutoModelForCausalLM.from_pretrained(params["model"]["name"], dtype=dtype).to(device)
    n_layers = model.config.num_hidden_layers
    if params["train"].get("gradient_checkpointing"):
        # Активации 28 слоёв не храним, а пересчитываем на обратном проходе:
        # памяти в разы меньше, шаг примерно на треть дольше.
        model.config.use_cache = False
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    model = get_peft_model(model, lora_config(params, n_layers, variant["freeze_first"]))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    model.train()

    examples = train_blob["examples"]
    micro_per_epoch = math.ceil(len(examples) / tcfg["batch_size"])
    steps_per_epoch = math.ceil(micro_per_epoch / tcfg["grad_accum"])
    total_steps = steps_per_epoch * tcfg["epochs"]
    if max_steps:
        total_steps = min(total_steps, max_steps)

    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=tcfg["lr"], weight_decay=tcfg["weight_decay"],
    )
    scheduler = get_cosine_schedule_with_warmup(
        optimizer, max(1, int(total_steps * tcfg["warmup_ratio"])), total_steps
    )

    eval_bs = tcfg.get("eval_batch_size", tcfg["batch_size"])
    val_examples = val_blob["examples"]

    def timed_eval() -> float:
        """Замер val loss; время копится отдельно, чтобы не попасть в seconds обучения."""
        nonlocal eval_seconds
        t = time.perf_counter()
        value = evaluate(model, val_examples, pad_id, device, eval_bs)
        eval_seconds += time.perf_counter() - t
        return value

    eval_seconds = 0.0
    curve_train: list[list[float]] = []
    curve_val: list[list[float]] = []
    # Точка «до обучения»: без неё нечем измерить, помогло ли дообучение.
    # LoRA-матрица B инициализирована нулями, так что это лосс базовой модели.
    base_val = timed_eval()
    curve_val.append([0, round(base_val, 4)])
    print(f"  шаг 0: val {base_val:.4f} (до обучения)")
    base_eval_seconds = eval_seconds   # замер до started: из времени обучения вычитать не нужно
    print(f"[{args.variant}] устройство {device}, обучаемых {trainable:,} из {total:,} "
          f"({trainable / total:.3%}); шагов {total_steps}")

    peak = allocated_bytes(device)
    started = time.perf_counter()
    step, micro, accum_loss, diverged = 0, 0, 0.0, False
    for epoch in range(tcfg["epochs"]):
        for batch in batches(examples, tcfg["batch_size"], pad_id, shuffle=True, seed=tcfg["seed"] + epoch):
            batch = {k: v.to(device) for k, v in batch.items()}
            loss = masked_loss(model, batch) / tcfg["grad_accum"]
            loss.backward()
            accum_loss += loss.item()
            micro += 1
            peak = max(peak, allocated_bytes(device))
            if micro % tcfg["grad_accum"]:
                continue
            torch.nn.utils.clip_grad_norm_(model.parameters(), tcfg["max_grad_norm"])
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            if device.type == "mps":
                torch.mps.empty_cache()   # длины примеров разные, кэш аллокатора фрагментируется
            step += 1
            curve_train.append([step, round(accum_loss, 4)])
            if not math.isfinite(accum_loss):
                diverged = True
                print(f"  шаг {step}: лосс {accum_loss} — обучение разошлось, останавливаюсь")
                break
            accum_loss = 0.0
            if step % tcfg["eval_every"] == 0 or step == total_steps:
                val = timed_eval()
                curve_val.append([step, round(val, 4)])
                print(f"  шаг {step}/{total_steps}: train {curve_train[-1][1]:.4f}, val {val:.4f}")
            if step >= total_steps:
                break
        if diverged or step >= total_steps:
            break
    if curve_val[-1][0] != step:   # обучение остановлено не на кратном eval_every шаге
        curve_val.append([step, round(timed_eval(), 4)])
    seconds = time.perf_counter() - started - (eval_seconds - base_eval_seconds)   # чистое обучение, без замеров val

    out_root = Path(args.out) if args.out else Path(params["paths"]["models"])
    adapter_dir = out_root / f"adapter_{args.variant}"
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)   # токенайзер едет вместе с адаптером

    tokens = sum(len(e["input_ids"]) for e in examples) * tcfg["epochs"]
    metrics = {
        "variant": args.variant,
        "freeze_first": variant["freeze_first"],
        "model": params["model"]["name"],
        "device": device.type,
        "dtype": params["model"]["dtype"],
        "seed": tcfg["seed"],
        "lr": tcfg["lr"],
        "effective_batch": tcfg["batch_size"] * tcfg["grad_accum"],
        "steps": step,
        "trainable_params": trainable,
        "total_params": total,
        "trainable_share": round(trainable / total, 6),
        "base_val_loss": base_val,
        "final_val_loss": curve_val[-1][1] if curve_val else None,
        "diverged": diverged,
        "curve_train": curve_train,
        "curve_val": curve_val,
        "seconds": round(seconds, 1),
        "eval_seconds": round(eval_seconds, 1),
        "seconds_per_step": round(seconds / max(step, 1), 3),
        "train_tokens_per_sec": round(tokens * min(1.0, step / max(total_steps, 1)) / seconds, 1) if seconds else 0,
        "peak_memory_mb": round(peak / 1048576, 1),
        "memory_metric": memory_metric(device),
        "adapter_dir": str(adapter_dir),
        "adapter_size_mb": dir_size_mb(adapter_dir),
        "inputs_fingerprint": inputs_fingerprint(params),
    }
    mdir = out_root / "metrics" if args.out else Path(params["paths"]["metrics"])
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / f"train_{args.variant}.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[{args.variant}] {step} шагов за {seconds:.0f} с; "
          f"пик памяти {metrics['peak_memory_mb']:.0f} МБ; адаптер {metrics['adapter_size_mb']} МБ -> {adapter_dir}")


if __name__ == "__main__":
    main()
