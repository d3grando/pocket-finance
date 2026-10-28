"""Per-message parse state and result helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

from app.schemas.intents import ParseContext, ParseResult
from app.services.parser.amounts import AmountTok, fill_raw, scan_amounts
from app.services.parser.dates import DateHit, find_dates, find_time
from app.services.parser.refs import Resolver
from app.services.parser.text import Token

_KEEP_ROLES = {"time", "amount", "amount_sign", "qty"}


@dataclass(slots=True)
class State:
    orig: str
    norm: str
    toks: list[Token]
    ctx: ParseContext
    R: Resolver
    dates: list[DateHit] = field(default_factory=list)
    time: time | None = None
    amounts: list[AmountTok] = field(default_factory=list)
    prepared: bool = False

    @property
    def today(self) -> date:
        return self.ctx.today

    def prepare(self) -> None:
        """Consume times, dates, amounts over the whole message (idempotent)."""
        if self.prepared:
            return
        self.prepared = True
        t = find_time(self.norm)
        if t is not None:
            self.time, s, e = t
            for tok in self.toks:
                if tok.start >= s and tok.end <= e:
                    tok.role = "time"
        nums = sum(1 for tok in self.toks if tok.kind == "num" and tok.free)
        self.dates = find_dates(self.norm, self.today, other_numbers=nums >= 2)
        for k, h in enumerate(self.dates):
            for tok in self.toks:
                if tok.free and tok.start >= h.start and tok.end <= h.end:
                    tok.role = f"date:{k}"
        self.amounts = scan_amounts(self.toks, self.norm, known_assets=self.R.known_assets,
                                    not_tickers=self.R.vocab)
        fill_raw(self.amounts, self.orig)

    def reset(self) -> None:
        """Forget roles set by a handler that gave up (keeps time/date/amount pre-pass)."""
        for tok in self.toks:
            r = tok.role
            if r and r not in _KEEP_ROLES and not r.startswith("date:"):
                tok.role = ""

    def amounts_in(self, idxs: list[int]) -> list[AmountTok]:
        s = set(idxs)
        return [a for a in self.amounts if a.token_idx[0] in s or a.token_idx[-1] in s]

    def dates_in(self, idxs: list[int]) -> list[DateHit]:
        found: list[DateHit] = []
        for i in idxs:
            r = self.toks[i].role
            if r.startswith("date:"):
                h = self.dates[int(r[5:])]
                if h not in found:
                    found.append(h)
        return found


def llm(reason: str, *, confidence: float = 0.3, intents: list | None = None,
        partial_ok: bool = False, unparsed: str | None = None) -> ParseResult:
    return ParseResult(intents=intents or [], needs_llm=True, confidence=confidence,
                       partial_ok=partial_ok, reason=reason, unparsed=unparsed)


def done(intents: list, confidence: float = 0.95, reason: str | None = None) -> ParseResult:
    return ParseResult(intents=intents, needs_llm=False, confidence=round(confidence, 2),
                       partial_ok=False, reason=reason)
