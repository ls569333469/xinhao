"""Additive exchange-contract identity adapter, no market-rule changes.

The original S1 schema accepted ASCII tickers only. Existing Binance source
contains Unicode alphanumeric contract names. Keep their exact bytes/legacy
UUID; never rename a symbol or silently drop its history.
"""
VERSION='tracking-identity-unicode-v1'
def valid_symbol(symbol):
    return isinstance(symbol,str) and 1<=len(symbol)<=64 and symbol==symbol.upper() and all(c.isalnum() or c=='_' for c in symbol)

def identity_errors(errors,record):
    symbol=record.get('symbol')
    if valid_symbol(symbol):
        return [e for e in errors if not (e.get('code')=='pattern' and e.get('path')=='event.symbol')]
    return errors+[dict(code='IDENTITY_symbol_invalid')] if not any(e.get('path')=='event.symbol' for e in errors) else errors
