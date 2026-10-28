"""Questions → StatsIntent / SearchIntent: «сколько я потратил сегодня?»,
«на что больше всего денег ушло за месяц?», «покажи траты дороже 100 долларов»,
«сколько чистыми заработал за октябрь?», «какие мои самые дорогие покупки?», «баланс»…
Anything not understood → needs_llm (no guessing)."""

from __future__ import annotations

from app.schemas.common import PeriodSpec
from app.schemas.intents import ParseResult, SearchFilter, SearchIntent, StatsIntent
from app.services.parser.currencies import detect_currency
from app.services.parser.ops import amount_of, resolve_currency
from app.services.parser.periods import PeriodHit, find_periods
from app.services.parser.segments import Seg, analyze
from app.services.parser.state import State, done, llm

QUESTION_START = {
    "сколько", "какие", "какой", "какая", "каких", "какую", "каков", "что", "куда", "где",
    "когда", "покажи", "показать", "покажите", "выведи", "найди", "найти", "дай", "список",
    "статистика", "статистику", "стата", "стату", "отчет", "сводка", "сводку", "итоги",
    "сравни", "сравнить", "топ", "how", "what", "show", "list", "find", "compare", "top",
    "есть", "был", "были", "много", "траты", "расходы", "доходы", "баланс", "балансы",
    "остаток", "остатки", "аналитика", "summary", "stats",
}
GENERAL_START = {"как", "почему", "зачем", "стоит", "можно", "посоветуй", "объясни", "why",
                 "should", "explain", "что такое"}
_FILLERS = set("""
сколько я мы мне меня у всего всё все денег деньги деньжат было были был на за в во по с со из
от до и а что какие какой какая какую каких какое мои мой моя моих моим мою моё покажи показать
покажите выведи найди найти дай список статистика статистику стата стату отчет сводка сводку
итоги итог траты трат тратах тратам расходы расход расходов расходах доходы доход доходов
доходах операции операций транзакции транзакций записи записей ушло ушли ушел потрачено итого
чистыми чистая чистый чистого прибыль сумма сумму общая общие самые самых самый самая самое
дорогие дорогих дорогая дорогой крупные крупных крупная большие больших дешевые дешевых мелкие
топ последние последних недавние сравни сравнить сравнение сравнению чем средний среднем средне
среднее день дням месяцам неделям категориям категории категорий куда где когда ли же вообще
бизнес бизнеса бизнесе личные личное личных личного баланс балансы остаток остатки осталось
счетах счету счете счетам счета дороже дешевле больше меньше более менее свыше выше ниже
неразобранные неразобранных разобрать без это how much many what did do i my me show list find
spend spent earn earned on in for the a all total expenses expense income top biggest largest
most over above under below than more less больше всего покупки покупок покупка трата
поступления поступлений тратил тратила потратил потратила потратили заработал заработала
заработали получил получила получили зарабатываю трачу потратить денег-то вышло вышли
каждый каждого посмотреть посмотри глянь подскажи скажи узнать нужно хочу знать есть ка-кие
статистикой аналитика аналитику summary stats там тут уже ещё еще всего-то примерно около
""".split())
_COMPARE_MIN = {"дороже", "больше", "более", "свыше", "выше", "от", "over", "above", "more",
                ">", ">="}
_COMPARE_MAX = {"дешевле", "меньше", "менее", "ниже", "до", "under", "below", "less", "<", "<="}


def is_question(st: State) -> bool:
    st.prepare()
    words = [t.text for t in st.toks if t.kind == "word"]
    if not words:
        return "?" in st.norm
    if "?" in st.norm:
        return True
    first = words[0] if words[0] not in ("а", "ну", "и", "так", "слушай", "скажи") else (
        words[1] if len(words) > 1 else words[0])
    if first in ("баланс", "балансы", "остаток", "остатки"):
        return not st.amounts
    if first in ("траты", "расходы", "доходы", "есть", "был", "были", "много", "бизнес",
                 "личные", "личное", "покупки", "последние", "операции", "история",
                 "неразобранные", "переводы", "самые", "самых", "крупнейшие", "крупные"):
        return not st.amounts
    if st.norm.startswith(("на что", "куда")):
        return True
    return first in QUESTION_START


def _mark_periods(st: State) -> list[PeriodHit]:
    hits = find_periods(st.norm, st.today)
    for h in hits:
        for t in st.toks:
            if t.start >= h.start and t.end <= h.end and t.kind != "punct":
                t.role = "period"
    return hits


def _has(words: set[str], *prefixes: str) -> bool:
    return any(w.startswith(prefixes) for w in words)


def parse_question(st: State) -> ParseResult | None:
    if not is_question(st):
        return None
    st.prepare()
    words_list = [t.text for t in st.toks if t.kind == "word"]
    words = set(words_list)
    if words_list and words_list[0] in GENERAL_START:
        return llm("general_question", confidence=0.3)
    if " что такое " in f" {st.norm} " or "what is" in st.norm:
        return llm("general_question", confidence=0.3)
    periods = _mark_periods(st)
    # numbers inside periods («с 1 по 15 октября», «за последние 10 дней») are not money
    st.amounts = [a for a in st.amounts if all(st.toks[i].role != "period" for i in a.token_idx)]
    # «5» in «топ 5» / «последние 10» is a limit, not money
    limit = None
    for a in list(st.amounts):
        i0 = a.token_idx[0]
        prev = st.toks[i0 - 1].text if i0 > 0 else ""
        nxt = st.toks[a.token_idx[-1] + 1].text if a.token_idx[-1] + 1 < len(st.toks) else ""
        if (a.currency is None and a.mult is None and a.number.isdigit() and int(a.number) <= 100
                and (prev in ("топ", "top", "последние", "последних", "first", "last")
                     or nxt.startswith(("самых", "последн", "крупн", "дорог", "больш")))):
            limit = int(a.number)
            for i in a.token_idx:
                st.toks[i].role = "limit"
            st.amounts.remove(a)
    idxs = [i for i, t in enumerate(st.toks) if t.kind != "punct"]
    seg = analyze(st, idxs)
    # amount filters
    min_amount = max_amount = amount_cur = None
    for a in seg.amounts:
        i0 = a.token_idx[0]
        prev_t = st.toks[i0 - 1] if i0 > 0 else None
        if a.sign_idx is not None and a.sign_idx > 0:
            prev_t = st.toks[a.sign_idx - 1]
        prev = prev_t.text if prev_t is not None else ""
        cur, problem = resolve_currency(st, a, None)
        if cur is None:
            return llm(problem or "ambiguous_currency", confidence=0.3)
        v, problem = amount_of(st, a, cur)
        if v is None:
            return llm(problem or "bad_amount", confidence=0.3)
        if prev in _COMPARE_MIN:
            min_amount, amount_cur = v, cur
        elif prev in _COMPARE_MAX:
            max_amount, amount_cur = v, cur
        else:
            return llm("question_amount", confidence=0.3)
        if prev_t is not None:
            prev_t.role = "cmp"
    display_cur = None
    for t in st.toks:
        if t.kind == "word" and t.free and len(t.text) > 2 and (c := detect_currency(t.text)):
            if t.text not in ("тон", "сол", "нот"):
                display_cur = c
                t.role = "display"
    unknown = _unknown(st, seg)
    period = periods[0].period if periods else None
    acc_refs = [m.ref.name for m in seg.accounts]
    biz = True if _has(words, "бизнес") else (False if _has(words, "личн") else None)
    verbs = set(seg.verbs)
    exp_cats = _cat_names(st, seg.cat_exp)
    inc_cats = _cat_names(st, seg.cat_inc)
    expensive = _has(words, "дорог", "крупн", "больш", "biggest", "largest", "expensive")
    cheap = _has(words, "дешев", "мелк", "cheapest", "smallest")
    is_income_q = bool(verbs & {"income"}) or _has(words, "доход", "поступлен", "заработ")
    kinds = ["income"] if is_income_q else (["expense"] if _has(
        words, "трат", "расход", "покуп", "потрат", "expense", "spent") else None)
    first = words_list[0] if words_list else ""
    # a merchant filter only for learned rules (a built-in brand → its category filter)
    learned = [m for m in seg.merchants if not m.builtin]
    search_exp_cats = exp_cats
    if learned:
        m_idx = {i for i in idxs if st.toks[i].role == "merchant"} | seg.brand_idx
        search_exp_cats = _cat_names(st, [(m, ix) for m, ix in seg.cat_exp
                                          if not set(ix) <= m_idx])

    def search(sort: str = "date_desc", lim: int | None = None) -> ParseResult:
        f = SearchFilter(
            period=period, kinds=kinds, category_refs=inc_cats if is_income_q
            else search_exp_cats,
            account_refs=acc_refs, min_amount=min_amount, max_amount=max_amount,
            amount_currency=amount_cur, counterparty=("@" + seg.handles[0]) if seg.handles
            else None, merchant_ref=learned[0].name if learned else None,
            is_business=biz, needs_account=True if _has(words, "неразобран", "разобрат")
            else None, sort=sort, limit=lim or limit or 20,
            text=" ".join(t.raw for t in unknown)[:200] or None,
        )
        return done([SearchIntent(filter=f)], 0.9)

    def stats(metric: str, conf: float = 0.9, **kw) -> ParseResult:
        intent = StatsIntent(
            metric=metric, period=period or PeriodSpec(), category_refs=inc_cats
            if metric == "earned" else exp_cats, account_refs=acc_refs, is_business=biz,
            display_currency=display_cur, limit=limit, **kw)
        if unknown:
            return llm("unknown_words", confidence=0.4, intents=[intent],
                       unparsed=" ".join(t.raw for t in unknown))
        return done([intent], conf)

    if (_has(words, "баланс", "остат", "balance") or "осталось" in words or (
            "сколько" in words and ("денег" in words or seg.accounts) and not verbs)):
        return stats("balance")
    if _has(words, "сравн", "compare"):
        cmp = periods[1].period if len(periods) > 1 else None
        return stats("compare", compare_period=cmp)
    if min_amount is not None or max_amount is not None:
        return search("amount_desc" if expensive else "date_desc")
    # «топ 3 покупки за октябрь», «самые дорогие покупки» → the top answer (DESIGN §9.2f:
    # ≤ 5 lines, «Крупнейшие покупки …»); filters stats cannot express → a search list
    top_ok = (not is_income_q and not cheap and not seg.handles and not learned
              and not unknown and kinds in (None, ["expense"]))
    if "топ" in words or "top" in words:
        kinds = kinds or (["income"] if is_income_q else ["expense"])
        if top_ok:
            return stats("top")
        return search("amount_asc" if cheap else "amount_desc", limit or 10)
    if expensive and (_has(words, "сам", "most") or limit):
        kinds = kinds or (["income"] if is_income_q else ["expense"])
        if top_ok:
            return stats("top")
        return search("amount_desc", limit or 10)
    if cheap and _has(words, "сам"):
        kinds = kinds or ["expense"]
        return search("amount_asc", limit or 10)
    finder = first in ("найди", "найти", "find", "поищи", "search")
    lister = first in ("покажи", "выведи", "список", "show", "list", "показать", "покажите",
                       "последние", "операции", "история", "переводы", "неразобранные")
    stats_noun = _has(words, "трат", "расход", "доход", "статист", "стат", "итог", "сводк",
                      "expense", "spent", "income")
    if finder or (lister and (seg.handles or seg.merchants or unknown or not stats_noun or _has(
            words, "операци", "транзакц", "запис", "последн", "перевод", "все", "неразобран",
            "обмен", "возврат"))):
        if _has(words, "перевод") and not seg.handles:
            kinds = ["transfer"]
        if _has(words, "обмен"):
            kinds = ["exchange"]
        if _has(words, "возврат"):
            kinds = ["refund"]
        return search()
    if _has(words, "чистым", "чистая", "чистый", "чистой", "прибыл", "profit", "net", "поток",
            "кэшфлоу", "cashflow", "сальдо", "плюсе", "минусе"):
        return stats("net")
    if _has(words, "средн", "average", "avg"):
        return stats("daily_average")
    if "дням" in words or "daily" in words:
        return stats("by_day")
    if "месяцам" in words or "monthly" in words:
        return stats("by_month")
    if biz and not verbs and not _has(words, "трат", "расход", "доход"):
        return stats("business")
    if (("на" in words and "что" in words) or "куда" in words or _has(
            words, "категори", "статист", "стата", "стату", "структур", "распредел", "отчет",
            "сводк", "итог") or ("больше" in words and "всего" in words)):
        return stats("by_category")
    if is_income_q:
        return stats("earned")
    if kinds == ["expense"] or "сколько" in words or verbs & {"expense"}:
        return stats("spent")
    return llm("question", confidence=0.3)


def _unknown(st: State, seg: Seg) -> list:
    cover = {i for _, ix in seg.cat_exp + seg.cat_inc for i in ix}
    out = []
    for i in seg.idxs:
        t = st.toks[i]
        if t.kind != "word" or not t.free or i in cover:
            continue
        if t.text in _FILLERS or t.text.startswith(("сколько", "потрат", "заработ", "тратил")):
            continue
        out.append(t)
    return out


_GENERIC_NOUNS = {"покупки", "покупок", "покупка", "покупку", "покупках", "трата", "траты",
                  "трат", "расходы", "расход", "операции", "доходы", "доход"}


def _cat_names(st: State, matches) -> list[str]:
    """Category filters of a question; generic nouns («покупки», «траты») are not filters."""
    out: list[str] = []
    for m, ix in matches:
        if all(st.toks[i].text in _GENERIC_NOUNS for i in ix):
            continue
        if m.ref.name not in out:
            out.append(m.ref.name)
    return out
