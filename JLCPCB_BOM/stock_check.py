"""Read current supplier stock using the suppliers' website interfaces.

This module also runs as a subprocess for the stock dialog.  Each output line
is an independent result so completed lookups survive cancellation.
"""
import json
import re
import sys

import requests

FIELDS = ("jlcpcb", "idle", "lcsc")
JLC_DETAIL = "https://cart.jlcpcb.com/shoppingCart/smtGood/getComponentDetail"
IDLE_URL = ("https://jlcpcb.com/api/overseas-smt-component-order-platform/v1/"
            "overseasSmtComponentOrder/smtIdleOrderWeb/selectSmtIdlePublishStock")
LCSC_URL = "https://www.lcsc.com/product-detail/{}.html"


class MissingData(ValueError):
    """The supplier does not provide this part or stock value."""


def quantity(value):
    if value is None:
        raise MissingData("Stock quantity is missing")
    if type(value) is not int or value < 0:
        raise ValueError("Missing or invalid stock quantity")
    return value


def response_json(response):
    if response.status_code == 404:
        raise MissingData("Part not found")
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or result.get("code") != 200:
        raise ValueError("Supplier returned an unsuccessful response")
    return result.get("data")


def stock_and_prices(stock, prices):
    try:
        return {"value": quantity(stock), "prices": prices}
    except MissingData as exc:
        return {"missing": str(exc), "prices": prices}


def get_jlcpcb(session, part):
    data = response_json(session.get(JLC_DETAIL, params={"componentCode": part}, timeout=20))
    if data is None:
        raise MissingData("Part not found at JLCPCB")
    if not isinstance(data, dict) or data.get("componentCode") != part:
        raise ValueError("JLCPCB returned a different or invalid part")
    price_data = {name: data.get(name) for name in ("prices", "jlcPrices", "buyPrices", "componentPrices")}
    display_prices = next((price_data[name] for name in ("jlcPrices", "prices", "componentPrices")
                           if isinstance(price_data[name], list)), None)
    result = stock_and_prices(data.get("stockCount"), display_prices
                              if data.get("shopShowForeignPriceFlag") is not False else None)
    result["price_data"] = price_data
    return result


def get_idle(session, part):
    total_stock, page, count, offers = 0, 1, 0, []
    while page <= 1000:
        data = response_json(session.post(IDLE_URL, json={"componentCode": part, "pageNum": page,
                                                         "pageSize": 10}, timeout=20))
        if not isinstance(data, dict) or type(data.get("total")) is not int:
            raise ValueError("Invalid idle parts response")
        rows = data.get("list") or []
        if not isinstance(rows, list):
            raise ValueError("Invalid idle parts listings")
        for row in rows:
            if not isinstance(row, dict) or row.get("componentCode") != part:
                raise ValueError("Idle listing has a different or missing part")
            total_stock += quantity(row.get("availableStockNum"))
            offers.append({"availableStockNum": row["availableStockNum"], "priceList": row.get("priceList")})
            count += 1
        if not data.get("hasNextPage"):
            if count != data["total"]:
                raise ValueError("Incomplete idle parts listings; retry the check")
            return {"value": total_stock, "prices": offers}
        if not rows:
            raise ValueError("Incomplete idle parts listings")
        page += 1
    raise ValueError("Too many idle parts pages")


def get_lcsc(session, part):
    response = session.get(LCSC_URL.format(part), timeout=20)
    if response.status_code == 404:
        raise MissingData("Part not found at LCSC")
    response.raise_for_status()
    match = re.search(r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                      response.text, re.IGNORECASE | re.DOTALL)
    if "Page Not Found | LCSC Electronics" in response.text:
        raise MissingData("Part not found at LCSC")
    if not match:
        raise ValueError("LCSC product data not found")
    data = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("webData")
    if data is None:
        raise MissingData("LCSC product data is missing")
    if not isinstance(data, dict) or data.get("productCode") != part:
        raise ValueError("LCSC returned a different or invalid part")
    result = stock_and_prices(data.get("stockNumber"), data.get("productPriceList")
                              if data.get("isShowForeignPrice") is not False else None)
    result["minimum_quantity"] = data.get("minBuyNumber")
    return result


def run(jobs, output=sys.stdout):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/html, */*",
                            "Origin": "https://jlcpcb.com", "Referer": "https://jlcpcb.com/parts/in-stock-parts"})
    lookups = {"jlcpcb": get_jlcpcb, "idle": get_idle, "lcsc": get_lcsc}
    try:
        for part, field in jobs:
            result = {"part": part, "field": field}
            try:
                if not re.fullmatch(r"C\d+", part) or field not in lookups:
                    raise ValueError("Invalid stock lookup")
                result.update(lookups[field](session, part))
            except MissingData as exc:
                result["missing"] = str(exc)
            except Exception as exc:
                result["error"] = str(exc) or type(exc).__name__
            print(json.dumps(result, ensure_ascii=True), file=output, flush=True)
    finally:
        session.close()


if __name__ == "__main__":
    run(json.load(sys.stdin))
