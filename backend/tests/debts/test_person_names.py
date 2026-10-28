"""app.core.person_names — declension-insensitive person matching (docs/DEBTS.md §5.3)."""

from __future__ import annotations

import pytest

from app.core.person_names import name_key, nominative_guess, same_person


@pytest.mark.parametrize(
    "forms",
    [
        ["Коля", "Коле", "у Коли", "Колю", "Колей", "коля"],
        ["Петя", "Пете", "у Пети", "Петей"],
        ["Маша", "Маше", "у Маши", "Машу", "Машей"],
        ["Иван", "Ивану", "у Ивана", "Иваном", "Иване"],
        ["Андрей", "Андрею", "у Андрея", "Андреем"],
        ["Иван Петров", "Ивану Петрову", "у Ивана Петрова"],
        ["@vasya", "vasya"],
    ],
)
def test_same_person_across_cases(forms: list[str]) -> None:
    keys = [name_key(f) for f in forms]
    assert all(same_person(keys[0], k) for k in keys), keys


@pytest.mark.parametrize(
    ("a", "b"),
    [("Коля", "Петя"), ("Ян", "Яна"), ("Иван", "Иван Петров"), ("Маша", "Миша")],
)
def test_different_people(a: str, b: str) -> None:
    assert not same_person(name_key(a), name_key(b))


@pytest.mark.parametrize(
    ("form", "case", "name", "certain"),
    [
        ("Коля", "nom", "Коля", True),
        ("коля", "nom", "Коля", True),
        ("у Пети", "gen", "Петя", True),
        ("Маши", "gen", "Маша", True),
        ("Ивана", "gen", "Иван", True),
        ("Андрея", "gen", "Андрей", True),
        ("Лены", "gen", "Лена", True),
        ("Ивану", "dat", "Иван", True),
        ("Маше", "dat", "Маша", True),
        ("Коле", "dat", "Коля", False),
        ("Колю", "acc", "Коля", True),
    ],
)
def test_nominative_guess(form: str, case: str, name: str, certain: bool) -> None:
    assert nominative_guess(form, case) == (name, certain)  # type: ignore[arg-type]


def test_short_names() -> None:
    assert same_person(name_key("Оля"), name_key("Оле"))
    assert same_person(name_key("Аня"), name_key("у Ани"))
    assert not same_person(name_key("Ян"), name_key("Яна"))
