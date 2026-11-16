from fastapi import APIRouter, Query

from app.services import reconcile_service as svc
from app.services import source_view

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("/book")
def price_book():
    return svc.list_price_book()


@router.get("/history")
def history(sku_code: str | None = Query(default=None)):
    return svc.list_history(sku_code)


@router.get("/source/{sku_code}")
def price_source(sku_code: str):
    row = source_view.ticket_source_for_sku(sku_code)
    return row or {}
