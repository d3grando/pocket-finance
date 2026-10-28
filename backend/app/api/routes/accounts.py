"""Accounts, balances, onboarding, «мои аккаунты» REST."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.accounts import (
    AccountCreate,
    AccountOut,
    AccountPatch,
    BalancesOut,
    MyAccountCreate,
    MyAccountOut,
    OnboardingBalancesIn,
    OpeningBalanceIn,
)
from app.schemas.common import Id, OkOut
from app.services.accounts import balances
from app.services.accounts import service as accounts
from app.services.ledger.common import user_settings

router = APIRouter(tags=["accounts"])


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    principal: PrincipalDep,
    session: SessionDep,
    include_archived: bool = False,
    include_debts: bool = False,
):
    """Debt service accounts (kind=debt, docs/DEBTS.md §2) only with ``include_debts=true``."""
    return await balances.list_accounts_out(
        session, principal.user_id, include_archived=include_archived,
        include_debts=include_debts,
    )


@router.get("/accounts/balances", response_model=BalancesOut)
async def get_balances(principal: PrincipalDep, session: SessionDep, include_debts: bool = False):
    """Totals never include debt accounts, even with ``include_debts=true``."""
    return await balances.get_balances(session, principal.user_id, include_debts=include_debts)


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(body: AccountCreate, principal: PrincipalDep, session: SessionDep):
    acc = await accounts.create_account(session, principal, body)
    out = await balances.account_out(session, principal.user_id, acc.id)
    await session.commit()
    return out


@router.post("/accounts/onboarding", response_model=BalancesOut)
async def onboarding(body: OnboardingBalancesIn, principal: PrincipalDep, session: SessionDep):
    """Set opening balances from the first-run screen and mark onboarding_done."""
    for item in body.items:
        await accounts.set_opening_balance(
            session, principal, item.account_id, item.asset, item.amount
        )
    st = await user_settings(session, principal.user_id)
    if st is not None and not st.onboarding_done:
        st.onboarding_done = True
        st.version = (st.version or 1) + 1
    await session.flush()
    out = await balances.get_balances(session, principal.user_id)
    await session.commit()
    return out


@router.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(account_id: Id, principal: PrincipalDep, session: SessionDep):
    return await balances.account_out(session, principal.user_id, account_id)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: Id, body: AccountPatch, principal: PrincipalDep, session: SessionDep
):
    await accounts.update_account(session, principal, account_id, body)
    out = await balances.account_out(session, principal.user_id, account_id)
    await session.commit()
    return out


@router.delete("/accounts/{account_id}", response_model=OkOut)
async def delete_account(account_id: Id, principal: PrincipalDep, session: SessionDep):
    await accounts.delete_account(session, principal, account_id)
    await session.commit()
    return OkOut()


@router.put("/accounts/{account_id}/opening", response_model=AccountOut)
async def set_opening(
    account_id: Id, body: OpeningBalanceIn, principal: PrincipalDep, session: SessionDep
):
    await accounts.set_opening_balance(
        session, principal, account_id, body.asset, body.amount, body.opening_at
    )
    out = await balances.account_out(session, principal.user_id, account_id)
    await session.commit()
    return out


def _my_out(m) -> MyAccountOut:  # type: ignore[no-untyped-def]
    return MyAccountOut(id=m.id, handle=m.handle, label=m.label, account_id=m.account_id)


@router.get("/my-accounts", response_model=list[MyAccountOut])
async def list_my_accounts(principal: PrincipalDep, session: SessionDep):
    return [_my_out(m) for m in await accounts.list_my_accounts(session, principal.user_id)]


@router.post("/my-accounts", response_model=MyAccountOut, status_code=201)
async def add_my_account(body: MyAccountCreate, principal: PrincipalDep, session: SessionDep):
    m = await accounts.add_my_account(session, principal, body)
    out = _my_out(m)
    await session.commit()
    return out


@router.delete("/my-accounts/{my_account_id}", response_model=OkOut)
async def delete_my_account(my_account_id: Id, principal: PrincipalDep, session: SessionDep):
    await accounts.delete_my_account(session, principal, my_account_id)
    await session.commit()
    return OkOut()
