"""Оценка ответов и допуск в champion. Контракт: EVALUATION.md, tests/test_eval.py."""
import json
import math

MIN_GOLDEN = 50
MIN_SLICES = 3
MIN_BEHAVIORAL = 3
STAT_KEYS = ('n', 'correct', 'valid', 'errors', 'accuracy', 'format_rate')


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate keys')
    return dict(pairs)


def _parse(raw, choices):
    """Строгий ответ: JSON-объект с единственным полем answer, строкой из choices."""
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) != {'answer'}:
        return None
    answer = value['answer']
    return answer if isinstance(answer, str) and answer in choices else None


def _stats(items):
    n = len(items)
    correct = sum(1 for i in items if i['correct'])
    valid = sum(1 for i in items if i['valid'])
    errors = sum(1 for i in items if i['error'])
    return dict(n=n, correct=correct, valid=valid, errors=errors,
                accuracy=correct / n if n else 0.0, format_rate=valid / n if n else 0.0)


def score(rows, outputs):
    """Верните overall, slices и per_item. Не удаляйте плохие ответы из знаменателя."""
    if not rows:
        raise ValueError('Пустой набор заданий')
    row_ids = [r['id'] for r in rows]
    out_ids = [o['id'] for o in outputs]
    if len(set(row_ids)) != len(row_ids):
        raise ValueError('Дублирующиеся id в заданиях')
    if len(set(out_ids)) != len(out_ids):
        raise ValueError('Дублирующиеся id в ответах')
    if set(row_ids) != set(out_ids):
        raise ValueError('id заданий и ответов не совпадают')
    by_id = {o['id']: o for o in outputs}

    per_item = []
    for row in rows:
        out = by_id[row['id']]
        error = out.get('error')
        raw = out.get('raw')
        raw = raw if isinstance(raw, str) else ''
        parsed = None if error else _parse(raw, row['choices'])
        valid = parsed is not None
        per_item.append(dict(id=row['id'], raw=raw, parsed=parsed, valid=valid,
                             correct=valid and parsed == row['expected'],
                             error=error or None, slices=list(row['slices'])))

    names = sorted({s for r in rows for s in r['slices']})
    return dict(overall=_stats(per_item),
                slices={s: _stats([i for i in per_item if s in i['slices']]) for s in names},
                per_item=per_item)


def _fail(message):
    raise ValueError(message)


def _check_stats(stats, label):
    """Метрики конечны, являются числами и соответствуют counts."""
    if not isinstance(stats, dict) or any(k not in stats for k in STAT_KEYS):
        _fail(f'{label}: нет полей {STAT_KEYS}')
    for key in STAT_KEYS:
        value = stats[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            _fail(f'{label}: {key} не конечное число')
    n, correct, valid, errors = (stats[k] for k in ('n', 'correct', 'valid', 'errors'))
    if n <= 0 or not 0 <= correct <= valid <= n or not 0 <= errors <= n:
        _fail(f'{label}: counts противоречивы')
    if not math.isclose(stats['accuracy'], correct / n, abs_tol=1e-9):
        _fail(f'{label}: accuracy не соответствует counts')
    if not math.isclose(stats['format_rate'], valid / n, abs_tol=1e-9):
        _fail(f'{label}: format_rate не соответствует counts')


def _check_report(report, role):
    if not isinstance(report, dict) or report.get('status') != 'complete':
        _fail(f'{role}: отчёт неполный (status != complete)')
    if not isinstance(report.get('model'), dict) or not report['model']:
        _fail(f'{role}: не указана конкретная модель')
    for key in ('overall', 'slices', 'per_item', 'behavioral'):
        if key not in report:
            _fail(f'{role}: нет поля {key}')
    _check_stats(report['overall'], f'{role}.overall')
    if not isinstance(report['slices'], dict):
        _fail(f'{role}: slices не словарь')
    for name, stats in report['slices'].items():
        _check_stats(stats, f'{role}.slices[{name}]')
    items = report['per_item']
    if not isinstance(items, list) or len(items) != report['overall']['n']:
        _fail(f'{role}: per_item не соответствует overall.n')
    ids = [i.get('id') for i in items]
    if len(set(ids)) != len(ids):
        _fail(f'{role}: дубли id в per_item')
    for key in ('correct', 'valid'):
        if sum(1 for i in items if i.get(key)) != report['overall'][key]:
            _fail(f'{role}: per_item.{key} не соответствует overall')
    if any(i.get('error') for i in items):
        _fail(f'{role}: ошибки генерации в per_item')
    behavioral = report['behavioral']
    if not isinstance(behavioral, dict) or 'overall' not in behavioral:
        _fail(f'{role}: нет behavioral.overall')
    _check_stats(behavioral['overall'], f'{role}.behavioral')


def quality_gate(baseline, candidate, critical_slices):
    """Верните {passed: True, ...} или вызовите ValueError с причиной отказа.

    Допуск ухудшения равен нулю: любое снижение, расхождение протокола,
    missing/NaN/ошибка генерации означает отказ.
    """
    if not isinstance(critical_slices, (list, tuple)) or not critical_slices:
        _fail('Не заданы критические срезы')
    _check_report(baseline, 'baseline')
    _check_report(candidate, 'candidate')

    for key in ('protocol_sha256', 'golden_sha256', 'baseline_spec_sha256'):
        a, b = baseline.get(key), candidate.get(key)
        if not isinstance(a, str) or not a or a != b:
            _fail(f'Несовместимые отчёты: {key}')

    n = baseline['overall']['n']
    if n != candidate['overall']['n']:
        _fail('Разный размер набора у baseline и candidate')
    if n < MIN_GOLDEN:
        _fail(f'Golden set: минимум {MIN_GOLDEN} примеров, в отчёте {n}')
    if len(baseline['slices']) < MIN_SLICES:
        _fail(f'Нужно минимум {MIN_SLICES} среза')
    if [i['id'] for i in baseline['per_item']] != [i['id'] for i in candidate['per_item']]:
        _fail('Разные id в per_item')
    if set(baseline['slices']) != set(candidate['slices']):
        _fail('Разные имена срезов')
    for name, stats in baseline['slices'].items():
        if stats['n'] != candidate['slices'][name]['n']:
            _fail(f'Разный размер среза {name}')

    for role, report in (('baseline', baseline), ('candidate', candidate)):
        if report['overall']['errors']:
            _fail(f'{role}: ошибки генерации ({report["overall"]["errors"]})')

    for metric in ('accuracy', 'format_rate'):
        if candidate['overall'][metric] < baseline['overall'][metric]:
            _fail(f'Регрессия {metric}: {candidate["overall"][metric]:.4f} < {baseline["overall"][metric]:.4f}')

    critical = {}
    for name in critical_slices:
        if name not in baseline['slices']:
            _fail(f'Критический срез отсутствует: {name}')
        a, b = baseline['slices'][name]['accuracy'], candidate['slices'][name]['accuracy']
        if b < a:
            _fail(f'Регрессия критического среза {name}: {b:.4f} < {a:.4f}')
        critical[name] = dict(baseline=a, candidate=b, n=baseline['slices'][name]['n'])

    behavior = candidate['behavioral']
    stats = behavior['overall']
    if stats['n'] < MIN_BEHAVIORAL:
        _fail(f'Behavioral: минимум {MIN_BEHAVIORAL} кейса')
    if stats['correct'] != stats['n'] or stats['errors']:
        _fail(f'Behavioral: прошли {stats["correct"]} из {stats["n"]}')
    if any(not i.get('correct') for i in behavior.get('per_item', [])):
        _fail('Behavioral: есть непройденный кейс в per_item')

    return dict(passed=True, n=n,
                accuracy=dict(baseline=baseline['overall']['accuracy'], candidate=candidate['overall']['accuracy']),
                format_rate=dict(baseline=baseline['overall']['format_rate'],
                                 candidate=candidate['overall']['format_rate']),
                critical_slices=critical,
                behavioral=dict(n=stats['n'], correct=stats['correct']))
