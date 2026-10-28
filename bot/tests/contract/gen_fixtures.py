"""Generate bot/tests/fixtures/contract.json from the REAL backend Pydantic schemas.

The bot venv cannot import the backend (aiogram pins pydantic < 2.14), so this script runs in a
throwaway env with the backend's pydantic and PYTHONPATH pointing at ``backend/``:

    cd bot && PYTHONPATH=../backend uv run --no-project --with pydantic==2.14.1 \
        python tests/contract/gen_fixtures.py

Every sample below is constructed and validated by the backend models and dumped with
``mode="json"`` — exactly what FastAPI sends. Bot tests render these payloads, so a contract
change in backend/app/schemas/* shows up as a fixture diff + failing bot tests.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from app.schemas.accounts import AccountOut
from app.schemas.assistant import AssistantResponse
from app.schemas.categories import CategoryOut, CategorySuggestions
from app.schemas.debts import DebtDetailOut, DebtOut, DebtsOut
from app.schemas.history import ActionOut, UndoResult
from app.schemas.outbox import OutboxClaimOut
from app.schemas.recurring import RecurringOut
from app.schemas.reports import ReportOut
from app.schemas.settings import SettingsOut
from app.schemas.stats import ByCategoryOut, SummaryOut
from app.schemas.transactions import MutationResult, TransactionOut, TransactionPage

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "contract.json"

A1 = uuid.UUID("6b0c0d3e-1111-4a2b-9c3d-000000000001")
A2 = uuid.UUID("6b0c0d3e-1111-4a2b-9c3d-000000000002")
A3 = uuid.UUID("6b0c0d3e-1111-4a2b-9c3d-000000000003")
UNDO1 = uuid.UUID("7c1d0e4f-2222-4b3c-8d4e-000000000001")
PREVIEW = uuid.UUID("8d2e1f50-3333-4c4d-9e5f-000000000001")
NOW = datetime(2026, 10, 27, 7, 32, 5, tzinfo=UTC)


def tx(id: int, *, kind="expense", amount="150000", currency="VND", category="Еда", cat_id=1,
       is_new=False, account: tuple[int, str] | None = (1, "Карта"), status="posted",
       title=None, action=A1, merchant=None, counterparty=None, description=None,
       account_amount=None, account_asset=None, is_estimated=False, amount_usd=None,
       on=date(2026, 10, 27), counter: tuple[int, str] | None = None,
       debt: tuple[int, str, str] | None = None) -> dict:
    data = {
        "id": id, "kind": kind, "status": status,
        "occurred_at": NOW.isoformat(), "occurred_on": on.isoformat(),
        "amount": amount, "currency": currency,
        "account": {"id": account[0], "name": account[1], "icon": "Landmark"} if account else None,
        "account_amount": account_amount or (amount if account else None),
        "account_asset": account_asset or (currency if account else None),
        "is_estimated": is_estimated,
        "category": {"id": cat_id, "name": category,
                     "kind": "income" if kind == "income" else "expense",
                     "icon": None, "color": None, "is_business": False, "is_new": is_new}
        if category else None,
        "merchant": {"id": merchant[0], "name": merchant[1]} if merchant else None,
        "counterparty": counterparty, "description": description,
        "amount_usd": amount_usd, "amount_vnd": amount if currency == "VND" else None,
        "source": "telegram", "raw_text": None, "created_action_id": str(action),
        "version": 1, "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
        "title": title or (category or ""),
        "counter_account": {"id": counter[0], "name": counter[1], "icon": "HandCoins"}
        if counter else None,
        "counter_amount": amount if counter else None,
        "counter_asset": currency if counter else None,
        "debt": {"id": debt[0], "name": debt[1], "op": debt[2]} if debt else None,
    }
    return TransactionOut.model_validate(data).model_dump(mode="json")


def bal(currency: str, amount: str, since: str | None, usd: str | None = None,
        vnd: str | None = None) -> dict:
    return {"currency": currency, "amount": amount,
            "direction": "i_owe" if amount.startswith("-") else "owed_to_me",
            "approx_usd": usd, "approx_vnd": vnd, "open_since": since}


def debt_out(id: int, name: str, balances: list[dict], *, status: str, account_id: int,
             aliases=(), note=None, archived=False, snoozed=None, last="2026-10-20",
             version=1) -> dict:
    return DebtOut.model_validate({
        "id": id, "name": name, "handle": None, "aliases": list(aliases), "note": note,
        "account_id": account_id, "is_archived": archived, "status": status,
        "balances": balances, "last_activity_on": last, "remind_snoozed_until": snoozed,
        "version": version, "created_at": NOW.isoformat()}).model_dump(mode="json")


def add_debts(fx: dict[str, object]) -> None:
    """Debts (docs/DEBTS.md §7–§9): lists, person card, cards with ``debt``, reminder."""
    kolya_b = [bal("VND", "300000", "2026-09-27", "11.48", "300000")]
    kolya = debt_out(5, "Коля", kolya_b, status="owed_to_me", account_id=41,
                     aliases=["коле", "коли"])
    vasya = debt_out(6, "Вася", [bal("USDT", "50", "2026-10-24", "50", "1310000")],
                     status="owed_to_me", account_id=43)
    lena_in = debt_out(8, "Лена <b>", [bal("USDT", "100", "2026-10-27", "100", "2620000")],
                       status="mixed", account_id=44)
    lena_out = debt_out(8, "Лена <b>", [bal("VND", "-200000", "2026-10-25", "7.6", "200000")],
                        status="mixed", account_id=44)
    petya = debt_out(7, "Петя", [bal("VND", "-1000000", "2026-10-20", "38.3", "1000000")],
                     status="i_owe", account_id=42)
    fx["debts_list"] = DebtsOut.model_validate({
        "owed_to_me": [kolya, vasya, lena_in], "i_owe": [petya, lena_out], "settled": [],
        "totals": {"owed_to_me_vnd": "4230000", "owed_to_me_usd": "161.48",
                   "i_owe_vnd": "1200000", "i_owe_usd": "45.9", "owed_to_me_count": 3,
                   "i_owe_count": 2, "missing_rates": False}}).model_dump(mode="json")
    fx["debts_vnd_only"] = DebtsOut.model_validate({
        "owed_to_me": [kolya], "i_owe": [],
        "totals": {"owed_to_me_vnd": "300000", "owed_to_me_usd": "11.48",
                   "owed_to_me_count": 1}}).model_dump(mode="json")
    fx["debts_empty"] = DebtsOut.model_validate({}).model_dump(mode="json")

    t_lend = tx(401, kind="transfer", amount="500000", category=None, account=(1, "Карта"),
                counter=(41, "Долг: Коля"), counterparty="Коля", title="Дал в долг: Коля",
                debt=(5, "Коля", "lend"), on=date(2026, 9, 27))
    t_repay = tx(402, kind="transfer", amount="200000", category=None,
                 account=(41, "Долг: Коля"), counter=(1, "Карта"), counterparty="Коля",
                 title="Возврат долга: Коля", debt=(5, "Коля", "repay_in"), action=A2,
                 on=date(2026, 10, 20))
    t_forgive = tx(404, kind="expense", amount="300000", category="Переводы людям", cat_id=20,
                   account=(41, "Долг: Коля"), counterparty="Коля", title="Списан долг: Коля",
                   debt=(5, "Коля", "forgive"), action=A3)
    fx["debt_detail"] = DebtDetailOut.model_validate({
        "debt": kolya, "total": 2, "limit": 10, "offset": 0,
        "history": [{"transaction": t_repay, "op": "repay_in", "currency": "VND",
                     "delta": "-200000", "balance_after": "300000"},
                    {"transaction": t_lend, "op": "lend", "currency": "VND",
                     "delta": "500000", "balance_after": "500000"}]}).model_dump(mode="json")
    fx["debt_detail_petya"] = DebtDetailOut.model_validate({
        "debt": petya, "total": 1, "limit": 10, "offset": 0,
        "history": [{"transaction": tx(405, kind="transfer", amount="1000000", category=None,
                                       account=(42, "Долг: Петя"), counter=(1, "Карта"),
                                       counterparty="Петя", title="Занял: Петя",
                                       debt=(7, "Петя", "borrow"), on=date(2026, 10, 20)),
                     "op": "borrow", "currency": "VND", "delta": "-1000000",
                     "balance_after": "-1000000"}]}).model_dump(mode="json")
    fx["debt_detail_settled"] = DebtDetailOut.model_validate({
        "debt": debt_out(5, "Коля", [], status="settled", account_id=41, version=4),
        "total": 3, "limit": 10, "offset": 0,
        "history": [{"transaction": t_forgive, "op": "forgive", "currency": "VND",
                     "delta": "-300000", "balance_after": "0"}]}).model_dump(mode="json")
    fx["debt_snoozed"] = debt_out(5, "Коля", kolya_b, status="owed_to_me", account_id=41,
                                  snoozed="2026-11-03", version=2)

    brief = {"debt_id": 5, "name": "Коля", "op": "lend", "currency": "VND",
             "balance_after": "500000", "excess": None}
    fx["created_debt_lend"] = resp([{
        "kind": "created", "title": "500 000 ₫ · Дал в долг: Коля",
        "subtitle": "Карта · Коля: долг тебе 500 000 ₫", "action_id": str(A1),
        "transaction": dict(t_lend, occurred_on="2026-10-27"), "debt": brief,
        "buttons": [btn("undo", "Отменить", "undo", action_id=str(A1)),
                    btn("edit", "Изменить", "edit", transaction_id=401)]}], action_ids=[A1])
    fx["created_debt_excess"] = resp([{
        "kind": "created", "title": "400 000 ₫ · Возврат долга: Коля",
        "subtitle": "Карта · долг закрыт", "note": "Излишек 100 000 ₫ — теперь ты должен: Коля",
        "action_id": str(A2), "transaction": dict(t_repay, amount="400000"),
        "debt": dict(brief, op="repay_in", balance_after="-100000", excess="100000"),
        "buttons": [btn("excess", "Излишек — мне", "send_text", "outline",
                        text="списать мой долг: Коля 100к"),
                    btn("undo", "Отменить", "undo", action_id=str(A2)),
                    btn("edit", "Изменить", "edit", transaction_id=402)]}], action_ids=[A2])
    t_vasya = tx(403, kind="transfer", amount="50", currency="USDT", category=None,
                 account=None, status="needs_account", counter=(43, "Долг: Вася"),
                 counterparty="Вася", amount_usd="50", title="Дал в долг: Вася",
                 debt=(6, "Вася", "lend"))
    fx["needs_account_debt"] = resp([{
        "kind": "needs_account", "title": "50 USDT · Дал в долг: Вася",
        "subtitle": "Вася: долг тебе 50 USDT · ≈ $50.00", "question": "С какого счёта?",
        "action_id": str(A1), "transaction": t_vasya,
        "debt": dict(brief, debt_id=6, name="Вася", currency="USDT", balance_after="50"),
        "options": [{"account_id": 3, "label": "Кошелёк", "hint": "есть USDT", "hotkey": "1"},
                    {"account_id": None, "label": "Без счёта", "hint": None, "hotkey": "0"}],
        "buttons": [btn("acc-3", "Кошелёк", "choose_account", "outline", transaction_id=403,
                        account_id=3),
                    btn("acc-none", "Без счёта", "choose_account", transaction_id=403,
                        account_id=None),
                    btn("later", "Позже", "later", transaction_id=403),
                    btn("undo", "Отменить", "undo", action_id=str(A1))]}], action_ids=[A1])
    t_vasya_in = tx(406, kind="transfer", amount="50", currency="USDT", category=None,
                    account=(43, "Долг: Вася"), status="needs_account", counterparty="Вася",
                    amount_usd="50", title="Возврат долга: Вася", debt=(6, "Вася", "repay_in"))
    fx["tx_debt_in_needs_account"] = t_vasya_in
    fx["tx_debt_lend_usdt"] = t_vasya
    fx["mutation_assign_debt_in"] = MutationResult.model_validate({
        "transaction": dict(t_vasya_in, status="posted",
                            counter_account={"id": 3, "name": "Кошелёк", "icon": None},
                            counter_amount="50", counter_asset="USDT"),
        "action_id": str(A2)}).model_dump(mode="json")
    fx["answer_debts"] = resp([{
        "kind": "answer", "title": "Долги", "answer_type": "balance",
        "headline": "Мне должны ≈ 4 230 000 ₫", "headline_approx": "≈ $161.48",
        "lines": [{"label": "Коля", "value": "долг тебе 300 000 ₫", "icon": "HandCoins"},
                  {"label": "Вася", "value": "долг тебе 50 USDT", "icon": "HandCoins"},
                  {"label": "Петя", "value": "ты должен 1 000 000 ₫", "icon": "HandCoins"}],
        "text": "Я должен ≈ 1 200 000 ₫", "debts": fx["debts_list"],
        "buttons": [btn("open", "Открыть долги →", "open", route="/accounts?debt=all")]}])
    fx["page_last_debts"] = TransactionPage.model_validate({
        "items": [t_forgive, t_repay, t_lend], "total": 3, "limit": 10, "offset": 0,
        "totals": {"expense_vnd": "300000", "income_vnd": "0", "expense_usd": "11.48",
                   "income_usd": "0", "count": 3}}).model_dump(mode="json")
    fx["outbox_debt_reminder"] = OutboxClaimOut.model_validate({"items": [
        {"id": 21, "kind": "debt_reminder", "telegram_user_id": 123456789, "attempts": 0,
         "payload": {"text": "⏰ Коля должен 300 000 ₫ уже 30 дней",
                     "buttons": [[{"text": "Записать возврат", "callback": "dr:5"},
                                  {"text": "Напомнить позже", "callback": "dz:5"}]]}},
        {"id": 22, "kind": "debt_reminder", "telegram_user_id": 123456789, "attempts": 0,
         "payload": {"text": "⏰ Твой долг · Петя: 1 000 000 ₫ уже 30 дней",
                     "buttons": [[{"text": "Записать возврат", "callback": "dr:7"},
                                  {"text": "Напомнить позже", "callback": "dz:7"}]]}}]}
    ).model_dump(mode="json")


def btn(id, label, kind, style="ghost", **action) -> dict:
    return {"id": id, "label": label, "style": style, "action": {"kind": kind, **action}}


def resp(cards: list[dict], route="parser", action_ids=()) -> dict:
    data = {"request_id": 812, "route": route, "cards": cards,
            "action_ids": [str(a) for a in action_ids], "changed": ["transactions", "stats"],
            "ai_available": True}
    return AssistantResponse.model_validate(data).model_dump(mode="json")


def main() -> None:
    t_food = tx(101, title="Еда")
    t_ton = tx(124, amount="3", currency="TON", category="Софт и AI 🆕", cat_id=15, is_new=True,
               account=(3, "Кошелёк"), counterparty="@alice", description="аккаунт GPT Pro",
               amount_usd="16.2", title="Софт и AI · @alice")
    t_usdt = tx(125, amount="15", currency="USDT", category="Подписки", cat_id=7, account=None,
                status="needs_account", amount_usd="15", title="Подписки")
    t_many = [tx(201, amount="120000", title="Еда", action=A1),
              tx(202, amount="45000", category="Такси/транспорт", cat_id=4, title="Такси",
                 action=A2),
              tx(203, amount="30000", category="Кофе", cat_id=3, title="Кофе", action=A3)]
    t_hl = [tx(301 + i, amount=a, category="Прочее", cat_id=12, merchant=(9, m),
               on=date(2026, 10, 21 + i)) for i, (a, m) in
            enumerate([("45000", "Highlands"), ("52000", "Highlands"), ("39000", "Phúc Long")])]

    fx: dict[str, object] = {}
    fx["created"] = resp([{
        "kind": "created", "title": "150 000 ₫ · Еда", "subtitle": "Карта · сегодня, 14:32",
        "action_id": str(A1), "transaction": t_food, "category_is_new": False,
        "buttons": [btn("undo", "Отменить", "undo", hotkey="Ctrl+Z", action_id=str(A1)),
                    btn("edit", "Изменить", "edit", transaction_id=101)]}], action_ids=[A1])
    fx["created_new_category"] = resp([{
        "kind": "created", "title": "3 TON · Софт и AI 🆕",
        "subtitle": "Кошелёк · @alice · «аккаунт GPT Pro»", "approx": "≈ $16.20",
        "category_is_new": True, "note": "Запомню: @alice → Софт и AI",
        "action_id": str(A1), "transaction": t_ton,
        "buttons": [btn("change-category", "В другую", "change_category", "outline",
                        transaction_id=124),
                    btn("undo", "Отменить", "undo", action_id=str(A1)),
                    btn("edit", "Изменить", "edit", transaction_id=124)]}], route="llm",
        action_ids=[A1])
    fx["created_many"] = resp([{
        "kind": "created_many", "title": "3 операции · −195 000 ₫", "subtitle": None,
        "action_ids": [str(A1), str(A2), str(A3)], "transactions": t_many,
        "total": "195000", "total_currency": "VND",
        "buttons": [btn(f"undo-{i}", "Отменить все", "undo", action_id=str(a))
                    for i, a in enumerate((A1, A2, A3))]
        + [btn("edit", "Изменить", "edit")]}], action_ids=[A1, A2, A3])
    fx["needs_account"] = resp([{
        "kind": "needs_account", "title": "15 USDT · Подписки", "subtitle": "≈ $15.00",
        "question": "С какого счёта?", "action_id": str(A1), "transaction": t_usdt,
        "options": [{"account_id": 3, "label": "Кошелёк", "hint": "есть USDT", "hotkey": "1"},
                    {"account_id": 4, "label": "Копилка", "hint": None, "hotkey": "2"},
                    {"account_id": None, "label": "Без счёта", "hint": None, "hotkey": "0"}],
        "buttons": [btn("acc-3", "Кошелёк", "choose_account", "outline", hotkey="1",
                        transaction_id=125, account_id=3),
                    btn("acc-4", "Копилка", "choose_account", "outline", hotkey="2",
                        transaction_id=125, account_id=4),
                    btn("acc-none", "Без счёта", "choose_account", hotkey="0",
                        transaction_id=125, account_id=None),
                    btn("later", "Позже", "later", transaction_id=125),
                    btn("undo", "Отменить", "undo", action_id=str(A1))]}], action_ids=[A1])
    fx["updated"] = resp([{
        "kind": "updated", "title": "250 000 ₫ · Еда", "subtitle": "Карта · сегодня, 14:32",
        "action_id": str(A2), "transaction": tx(101, amount="250000"),
        "changes": ["150 000 ₫ → 250 000 ₫"],
        "buttons": [btn("undo", "Отменить", "undo", action_id=str(A2)),
                    btn("edit", "Изменить", "edit", transaction_id=101)]}], action_ids=[A2])
    fx["deleted"] = resp([{
        "kind": "deleted", "title": "150 000 ₫ · Еда", "action_id": str(A2),
        "transaction": t_food,
        "buttons": [btn("undo", "Вернуть", "undo", action_id=str(A2))]}], action_ids=[A2])
    fx["preview"] = resp([{
        "kind": "preview", "title": "Изменю 3 операции",
        "preview_token": str(PREVIEW), "operation": "bulk_update",
        "change_summary": "категория: Прочее → Кофе",
        "items": [{"transaction": t, "before": "Прочее", "after": "Кофе"} for t in t_hl],
        "total_count": 3, "total": "Итого 136 000 ₫",
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
        "buttons": [btn("cancel", "Отмена", "cancel_preview", hotkey="Esc",
                        preview_token=str(PREVIEW)),
                    btn("confirm", "Применить", "confirm_preview", "primary", hotkey="Enter",
                        preview_token=str(PREVIEW))]}], route="llm")
    fx["applied"] = resp([{
        "kind": "applied", "title": "Изменено 3 операции", "action_id": str(A3), "count": 3,
        "buttons": [btn("undo", "Отменить", "undo", action_id=str(A3))]}], action_ids=[A3])
    fx["answer"] = resp([{
        "kind": "answer", "title": "Сегодня потрачено", "answer_type": "stats",
        "headline": "−595 000 ₫", "headline_approx": "≈ $22.76",
        "lines": [{"label": "Еда", "value": "320 000 ₫", "amount": "320000", "currency": "VND",
                   "share": 0.54, "category_id": 1, "icon": "UtensilsCrossed"},
                  {"label": "Такси/транспорт", "value": "120 000 ₫", "amount": "120000",
                   "currency": "VND", "share": 0.2, "category_id": 4, "icon": "CarTaxiFront"},
                  {"label": "<script>", "value": "85 000 ₫", "category_id": 99}],
        "total_count": 5,
        "buttons": [btn("open", "Открыть в аналитике →", "open",
                        route="/analytics?period=today")]}], route="parser")
    fx["unclear"] = resp([{
        "kind": "unclear", "title": "Не понял, что записать",
        "question": "«перевел вчера» — не хватает суммы.", "reason": "missing_amount",
        "buttons": [btn("opt-0", "Это расход…", "send_text", text="расход вчера 100к"),
                    btn("opt-1", "Это перевод…", "send_text", text="перевод вчера 100к"),
                    btn("manual", "Вручную", "manual", prefill={"text": "перевел вчера"})]}],
        route="llm")
    fx["error_ai"] = resp([{
        "kind": "error", "code": "ai_unavailable", "title": "AI сейчас недоступен",
        "text": "Сложную фразу не разобрал. Ничего не записано.",
        "buttons": [btn("manual", "Вручную", "manual"), btn("retry", "Повторить", "retry")]}],
        route="none")
    fx["undone"] = resp([{
        "kind": "undone", "title": "Отменено: 150 000 ₫ · Еда",
        "undone_action_id": str(A1), "undo_action_id": str(UNDO1),
        "buttons": [btn("redo", "Вернуть", "redo", action_id=str(UNDO1))]}])
    fx["balance"] = resp([{
        "kind": "balance", "title": "Карта: 5 200 000 ₫", "subtitle": "корректировка −120 000 ₫",
        "action_id": str(A1), "result": "adjusted",
        "account": AccountOut.model_validate({
            "id": 1, "name": "Карта", "kind": "bank", "currency": "VND", "is_multi_asset": False,
            "aliases": [], "default_for_currencies": ["VND"], "sort_order": 1,
            "is_archived": False, "version": 1, "assets": []}).model_dump(mode="json"),
        "buttons": [btn("undo", "Отменить", "undo", action_id=str(A1))]}])

    fx["tx_food"] = t_food
    fx["tx_ton"] = t_ton
    fx["tx_usdt"] = t_usdt
    fx["mutation_assign"] = MutationResult.model_validate({
        "transaction": dict(t_usdt, status="posted", account={"id": 3, "name": "Кошелёк"},
                            account_amount="15", account_asset="USDT"),
        "action_id": str(A2)}).model_dump(mode="json")
    fx["mutation_category"] = MutationResult.model_validate({
        "transaction": dict(t_ton, category=dict(t_ton["category"], id=7, name="Подписки",
                                                 is_new=False), version=2),
        "action_id": str(A3)}).model_dump(mode="json")
    fx["undo_result"] = UndoResult.model_validate({
        "status": "undone", "undone_action_id": str(A1), "undo_action_id": str(UNDO1),
        "summary": "Отменено: 150 000 ₫ · Еда", "changed": ["transactions"]}).model_dump(
        mode="json")
    fx["actions_recent"] = [ActionOut.model_validate({
        "id": str(A1), "kind": "create_transaction", "summary": "Еда 150 000 ₫",
        "source": "telegram", "input_text": "еда 150к",
        "entity_refs": [{"type": "transaction", "id": 101}], "is_undoable": True,
        "undone_at": None, "created_at": NOW.isoformat()}).model_dump(mode="json")]
    cats = [CategoryOut.model_validate({
        "id": i, "name": n, "kind": "expense", "is_business": b, "aliases": [],
        "is_system": True, "is_new": False, "is_archived": False, "sort_order": i,
        "version": 1}).model_dump(mode="json")
        for i, n, b in [(7, "Подписки", False), (14, "Серверы/инфра", True),
                        (16, "Закупка товара", True), (1, "Еда", False), (3, "Кофе", False),
                        (12, "Прочее", False), (9, "Покупки", False)]]
    fx["suggest"] = CategorySuggestions.model_validate({
        "similar": [{"category": cats[0], "score": 0.81}, {"category": cats[1], "score": 0.6}],
        "all": cats}).model_dump(mode="json")
    fx["accounts"] = [AccountOut.model_validate(a).model_dump(mode="json") for a in [
        {"id": 1, "name": "Карта", "kind": "bank", "currency": "VND", "is_multi_asset": False,
         "aliases": ["карта"], "default_for_currencies": ["VND"], "sort_order": 1,
         "is_archived": False, "version": 1, "assets": []},
        {"id": 3, "name": "Кошелёк", "kind": "crypto_wallet", "currency": None, "is_multi_asset": True,
         "aliases": ["Кошелёк"], "default_for_currencies": [], "sort_order": 3, "is_archived": False,
         "version": 1, "assets": [{"asset": "USDT", "opening_balance": "100", "opening_at": None,
                                   "balance": "85"}]},
        {"id": 4, "name": "Копилка", "kind": "crypto_wallet", "currency": None,
         "is_multi_asset": True, "aliases": [], "default_for_currencies": [], "sort_order": 4,
         "is_archived": False, "version": 1, "assets": []},
        {"id": 7, "name": "Карта RUB", "kind": "card", "currency": "RUB", "is_multi_asset": False,
         "aliases": [], "default_for_currencies": ["RUB"], "sort_order": 7, "is_archived": False,
         "version": 1, "assets": []}]]
    fx["page_needs_account"] = TransactionPage.model_validate({
        "items": [t_usdt], "total": 1, "limit": 10, "offset": 0,
        "totals": {"expense_vnd": "0", "income_vnd": "0", "expense_usd": "15",
                   "income_usd": "0", "count": 1}}).model_dump(mode="json")
    fx["page_last"] = TransactionPage.model_validate({
        "items": [t_food, t_ton, tx(150, kind="income", amount="300", currency="USDT",
                                    category="Клиенты", account=(3, "Кошелёк"), title="Клиенты")],
        "total": 42, "limit": 10, "offset": 0,
        "totals": {"expense_vnd": "150000", "income_vnd": "0", "expense_usd": "22",
                   "income_usd": "300", "count": 42}}).model_dump(mode="json")
    rng = {"date_from": "2026-10-01", "date_to": "2026-10-31", "label": "Октябрь 2026"}
    day = {"date_from": "2026-10-27", "date_to": "2026-10-27", "label": "Сегодня"}
    fx["summary"] = SummaryOut.model_validate({
        "currency": "VND", "today": day, "month": rng,
        "spent_today": {"value": "595000", "currency": "VND", "approx_usd": "22.76"},
        "spent_month": {"value": "12450000", "currency": "VND", "approx_usd": "476.3",
                        "change_ratio": "0.12"},
        "income_month": {"value": "18300000", "currency": "VND", "approx_usd": "700"},
        "net_month": {"value": "5850000", "currency": "VND", "approx_usd": "223.8"},
        "needs_account_count": 2, "missing_rates_count": 0}).model_dump(mode="json")
    items = [("Еда", "320000", "0.538"), ("Такси/транспорт", "120000", "0.202"),
             ("Кофе", "85000", "0.143"), ("Подписки", "40000", "0.067"),
             ("Связь", "30000", "0.05")]
    fx["by_category_today"] = ByCategoryOut.model_validate({
        "range": day, "kind": "expense", "currency": "VND", "total": "595000",
        "items": [{"category_id": i + 1, "name": n, "is_business": False, "amount": a,
                   "share": s, "count": 1} for i, (n, a, s) in enumerate(items)]}).model_dump(
        mode="json")
    fx["settings"] = SettingsOut.model_validate({
        "timezone": "Asia/Ho_Chi_Minh", "default_currency": "VND", "analytics_currency": "USD",
        "summary_currency": "VND", "small_amount_thousands": True, "theme": "system",
        "desktop_hotkey": "Ctrl+Alt+Space", "telegram_user_id": 123456789,
        "report_monthly": True, "report_daily": False, "report_weekly": False,
        "report_time": time(10, 0), "report_daily_time": time(22, 0), "report_weekly_day": 6,
        "report_weekly_time": time(21, 0), "reminders_enabled": True,
        "onboarding_done": True, "version": 3}).model_dump(
        mode="json")
    fx["recurring"] = [RecurringOut.model_validate({
        "id": 2, "title": "Аренда", "kind": "expense", "amount": "11000000", "currency": "VND",
        "account_id": 1, "category_id": 5, "merchant_id": None, "description": None,
        "frequency": "monthly", "interval": 1, "day_of_month": 5, "weekday": None,
        "month": None, "next_due_on": "2026-11-05", "remind_time": time(10, 0),
        "is_active": True, "last_reminded_on": "2026-10-05", "version": 4}).model_dump(
        mode="json")]
    fx["report_monthly"] = ReportOut.model_validate({
        "kind": "monthly", "range": rng,
        "text_html": "🗓 <b>Октябрь 2026</b>\n\nРасходы −12 450 000 ₫",
        "text_plain": "Октябрь 2026. Расходы −12 450 000 ₫"}).model_dump(mode="json")
    fx["outbox_claim"] = OutboxClaimOut.model_validate({"items": [
        {"id": 11, "kind": "recurring_reminder", "telegram_user_id": 123456789, "attempts": 0,
         "payload": {"text": "⏰ Сегодня обычно платишь\n<b>Аренда</b> — 11 000 000 ₫ · Карта",
                     "buttons": [[{"text": "✅ Записать", "callback": "r:2"},
                                  {"text": "⏭ Пропустить", "callback": "k:2"}],
                                 [{"text": "✏️ Другая сумма", "callback": "m:2"}]]}},
        {"id": 12, "kind": "report_monthly", "telegram_user_id": 123456789, "attempts": 0,
         "payload": {"text": "🗓 <b>Октябрь 2026</b>",
                     "buttons": [[{"text": "📊 Подробно", "callback": "o:analytics_last_month"},
                                  {"text": "📥 Разобрать", "callback": "i"}]]}},
        {"id": 13, "kind": "info", "telegram_user_id": 111, "attempts": 0,
         "payload": {"text": "чужой", "buttons": []}}]}).model_dump(mode="json")

    add_debts(fx)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(fx, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    # backend texts use NBSP in amounts (app.core.money.format_money): «150 000 ₫», «3 TON»
    while re.search(r"(\d) (\d{3})(?!\d)", text):
        text = re.sub(r"(\d) (\d{3})(?!\d)", "\\1\u00a0\\2", text)
    text = re.sub(r"(\d) (₫|₽|USDT|TON|BTC)", "\\1\u00a0\\2", text)
    text = text.replace("≈ $", "≈\u00a0$")
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(fx)} fixtures)", file=sys.stderr)


if __name__ == "__main__":
    main()
