"""Allowlisted Alpaca GET adapter. No account, position or order operations."""
from datetime import datetime, time, timedelta
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from core.theta_quote_execution import Contract, Quote
from core.theta_portfolio import stamp


class Provider:
    def __init__(self):
        self.calendars = {}
        self.metadata = {}

    def request(self, path, params):
        roots = {'/v2/options/contracts': 'https://paper-api.alpaca.markets',
                 '/v2/calendar': 'https://paper-api.alpaca.markets',
                 '/v1beta1/options/quotes/latest': 'https://data.alpaca.markets'}
        if path not in roots:
            raise ValueError('market_data_endpoint_forbidden')
        key = os.environ.get('ALPACA_ALGO_PLUS_KEY') or os.environ.get('ALPACA_ALGO_KEY') or os.environ.get('ALPACA_API_KEY')
        secret = os.environ.get('ALPACA_ALGO_PLUS_SECRET') or os.environ.get('ALPACA_ALGO_SECRET') or os.environ.get('ALPACA_API_SECRET')
        if not key or not secret:
            raise RuntimeError('alpaca_credentials_unavailable')
        req = urllib.request.Request(roots[path]+path+'?'+urllib.parse.urlencode(params),
                                     headers={'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        except Exception as exc:
            raise RuntimeError('alpaca_market_data_unavailable') from exc

    def session(self, day):
        if day not in self.calendars:
            rows = self.request('/v2/calendar', {'start': day, 'end': day})
            if not rows:
                return None
            if len(rows) != 1 or rows[0]['date'] != day:
                raise ValueError('calendar_mismatch')
            zone = ZoneInfo('America/New_York')
            self.calendars[day] = tuple(datetime.combine(datetime.fromisoformat(day).date(), time.fromisoformat(rows[0][k]), zone) for k in ('open', 'close'))
        return self.calendars[day]

    def contracts(self, ticker, expiry):
        params = {'underlying_symbols': ticker, 'root_symbol': ticker, 'expiration_date': expiry,
                  'expiration_date_gte': expiry, 'expiration_date_lte': expiry, 'limit': 10000,
                  'show_deliverables': 'true'}
        contracts = []
        for _ in range(10):
            payload = self.request('/v2/options/contracts', params)
            for row in payload.get('option_contracts') or []:
                if row.get('root_symbol') != ticker or row.get('expiration_date') != expiry or row.get('status') != 'active':
                    continue
                # No settlement defaults, no root substitution, no adjusted
                # contract multipliers. If metadata omits settlement we block.
                if str(row.get('size')) != '100':
                    continue
                settlement = row.get('settlement_class') or row.get('settlement_type')
                deliverables = row.get('deliverables') or []
                if (not settlement and row.get('style') == 'american' and len(deliverables) == 1
                        and deliverables[0].get('type') == 'equity'
                        and deliverables[0].get('symbol') == ticker
                        and str(deliverables[0].get('amount')) == '100'
                        and not deliverables[0].get('delayed_settlement')):
                    settlement = 'physical_american_standard_100'
                self.metadata[row['symbol']] = row
                contracts.append(Contract(row['symbol'], ticker, expiry, {'call': 'C', 'put': 'P'}[row['type']], float(row['strike_price']), str(settlement or '')))
            token = payload.get('next_page_token')
            if not token:
                return contracts
            if token == params.get('page_token'):
                break
            params['page_token'] = token
        raise RuntimeError('contract_pagination_incomplete')

    def contract_close(self, contract, day):
        if contract.symbol not in self.metadata:
            self.contracts(contract.underlying, contract.expiration)
        row = self.metadata.get(contract.symbol, {})
        # Exact contract session evidence is required, particularly AM-settled
        # index options. A stock calendar alone does not verify an option close.
        close = row.get('session_close')
        if close and stamp(close).astimezone(ZoneInfo('America/New_York')).date().isoformat() == day:
            return stamp(close)
        # Versioned reference: Nasdaq Options Market Hours lists these
        # classes as trading 15 minutes after the equity session. Calendar
        # dates/early closes still come from the provider, never a weekday guess.
        # https://www.nasdaqtrader.com/Trader.aspx?id=optionshours (2026-09-10)
        # https://www.nyse.com/markets/hours-calendars (eligible options 13:15)
        if contract.settlement == 'physical_american_standard_100' and contract.underlying in {'SPY', 'QQQ', 'IWM'}:
            session = self.session(day)
            return session[1] + timedelta(minutes=15) if session else None
        return None

    def quotes(self, symbols):
        payload = self.request('/v1beta1/options/quotes/latest', {'symbols': ','.join(symbols), 'feed': 'opra'})
        quotes = {}
        for symbol, q in (payload.get('quotes') or {}).items():
            try:
                quotes[symbol] = Quote(symbol, float(q['bp']), float(q['ap']), stamp(q['t']), int(q['bs']), int(q['as']))
            except (KeyError, ValueError, TypeError):
                continue
        return quotes
