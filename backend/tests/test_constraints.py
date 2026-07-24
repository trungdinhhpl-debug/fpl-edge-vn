"""FPL rule validators (spec §24: solver must obey current FPL rules)."""
from app.optimizer.constraints import (
    MAX_PER_CLUB,
    SQUAD_BY_TYPE,
    validate_squad,
    validate_xi,
)


def _accessors(types, prices, clubs):
    return (lambda i: types[i], lambda i: prices[i], lambda i: clubs[i])


def test_valid_squad_passes():
    # 2 GK, 5 DEF, 5 MID, 3 FWD across 5 clubs (<=3 each)
    ids = list(range(15))
    types = {i: t for i, t in enumerate(
        [1, 1] + [2] * 5 + [3] * 5 + [4] * 3)}
    clubs = {i: i % 5 for i in ids}      # max 3 per club
    prices = {i: 50 for i in ids}
    gt, gp, gc = _accessors(types, prices, clubs)
    assert validate_squad(ids, gt, gp, gc).valid


def test_wrong_position_count_fails():
    ids = list(range(15))
    types = {i: t for i, t in enumerate([1] * 3 + [2] * 4 + [3] * 5 + [4] * 3)}
    clubs = {i: i % 6 for i in ids}
    prices = {i: 50 for i in ids}
    gt, gp, gc = _accessors(types, prices, clubs)
    assert not validate_squad(ids, gt, gp, gc).valid


def test_max_per_club_enforced():
    ids = list(range(15))
    types = {i: t for i, t in enumerate([1, 1] + [2] * 5 + [3] * 5 + [4] * 3)}
    clubs = {i: 0 for i in ids}          # all same club -> illegal
    prices = {i: 50 for i in ids}
    gt, gp, gc = _accessors(types, prices, clubs)
    res = validate_squad(ids, gt, gp, gc)
    assert not res.valid
    assert any("Club" in e for e in res.errors)


def test_valid_formations():
    assert validate_xi([1] + [2] * 3 + [3] * 4 + [4] * 3)   # 3-4-3
    assert validate_xi([1] + [2] * 5 + [3] * 4 + [4] * 1)   # 5-4-1
    assert validate_xi([1] + [2] * 4 + [3] * 4 + [4] * 2)   # 4-4-2


def test_invalid_formations():
    assert not validate_xi([1] * 2 + [2] * 3 + [3] * 3 + [4] * 3)  # 2 GK
    assert not validate_xi([1] + [2] * 2 + [3] * 5 + [4] * 3)      # 2 DEF (<3)
    assert not validate_xi([1] + [2] * 3 + [3] * 4 + [4] * 2)      # only 10


def test_squad_template_totals_15():
    assert sum(SQUAD_BY_TYPE.values()) == 15
    assert MAX_PER_CLUB == 3
