"""Деньги.

Все суммы внутри программы - целые числа копеек (int). float не используется
нигде: суммы читаются из текста, складываются и делятся только как целые.
Деление на доли идёт методом наибольшего остатка, поэтому сумма долей всегда
равна исходной сумме до копейки.
"""

from __future__ import annotations

import re
from typing import List, Sequence

_AMOUNT_RE = re.compile(r"^(\d+)(?:[.,](\d{1,2}))?$")


def parse_money(text: str) -> int:
    """Сумма в рублях из выгрузки -> копейки: '100000.00' -> 10000000.

    Допускаются запятая вместо точки и пробелы между разрядами.
    Минус и больше двух знаков после запятой - ошибка: в выгрузке их быть
    не должно, а молча округлять деньги нельзя.
    """
    cleaned = text.strip().replace(" ", "").replace(" ", "")
    match = _AMOUNT_RE.match(cleaned)
    if not match:
        raise ValueError(f"не сумма в рублях: {text!r} (ожидается, например, 100000.00)")
    rubles = int(match.group(1))
    kopecks = int((match.group(2) or "").ljust(2, "0"))
    return rubles * 100 + kopecks


def format_money(kopecks: int, *, decimal_sep: str = ",", group_sep: str = " ") -> str:
    """Копейки -> текст. По умолчанию по-русски: 10000000 -> '100 000,00'."""
    sign = "-" if kopecks < 0 else ""
    rubles, rest = divmod(abs(kopecks), 100)
    rubles_text = f"{rubles:,}".replace(",", group_sep)
    return f"{sign}{rubles_text}{decimal_sep}{rest:02d}"


def split_proportional(total: int, weights: Sequence[int]) -> List[int]:
    """Делит total копеек пропорционально весам, сумма долей == total.

    Каждая доля сначала округляется вниз, оставшиеся копейки получают доли
    с наибольшим дробным остатком; при равных остатках - те, что раньше в списке.
    Позиции с нулевым весом не получают ничего.
    """
    if total < 0 or any(w < 0 for w in weights):
        raise ValueError("сумма и веса не могут быть отрицательными")
    weight_sum = sum(weights)
    if weight_sum == 0:
        if total == 0:
            return [0] * len(weights)
        raise ValueError("нельзя разделить ненулевую сумму по нулевым весам")
    shares = [total * w // weight_sum for w in weights]
    remainders = [total * w % weight_sum for w in weights]
    leftover = total - sum(shares)
    by_remainder = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
    for i in by_remainder[:leftover]:
        shares[i] += 1
    return shares


def allocate_capped(total: int, weights: Sequence[int], caps: Sequence[int]) -> List[int]:
    """Делит total пропорционально весам, но каждой позиции - не больше её cap.

    Если позиции по пропорции положено больше, чем её cap, она получает ровно
    cap, а остаток заново делится между остальными («водяной уровень»).
    Возвращает доли; их сумма == min(total, сумма cap у позиций с весом > 0).
    Что не поместилось, вызывающий код видит как разницу total - sum(долей).
    """
    if len(weights) != len(caps):
        raise ValueError("weights и caps разной длины")
    if total < 0 or any(c < 0 for c in caps):
        raise ValueError("сумма и лимиты не могут быть отрицательными")
    shares = [0] * len(weights)
    open_items = [i for i in range(len(weights)) if weights[i] > 0 and caps[i] > 0]
    rest = total
    while rest > 0 and open_items:
        proposal = split_proportional(rest, [weights[i] for i in open_items])
        capped = [i for i, s in zip(open_items, proposal) if s >= caps[i]]
        if not capped:
            for i, s in zip(open_items, proposal):
                shares[i] = s
            rest = 0
            break
        for i in capped:
            shares[i] = caps[i]
            rest -= caps[i]
        open_items = [i for i in open_items if i not in capped]
    return shares
