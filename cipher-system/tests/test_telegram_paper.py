from core.telegram_paper import connect, parse_entry, process, snapshot


def message(mid, text):
    return {"id": mid, "date": "2026-09-09T15:00:00+00:00", "text": text}


def test_parses_single_vertical_and_four_leg_iron_fly():
    one = parse_entry(1, message(1, "")["date"], "🛑 Blocked\nText: SPX 7685C at $2.05 for today - Lotto")
    assert one["symbol"] == "SPX" and len(one["legs"]) == 1 and one["entry_price"] == 2.05
    two = parse_entry(2, message(2, "")["date"], "Text: Sell SPX 7665P n Buy 7675P and collect $1.35 for today")
    assert len(two["legs"]) == 2 and two["price_style"] == "credit"
    four = parse_entry(3, message(3, "")["date"], "Text: Sell SPX 7690C/P, Buy 7670P and buy 7710C and collect $850 for today")
    assert len(four["legs"]) == 4 and four["entry_price"] == 8.5


def test_four_leg_position_is_atomic_and_quantity_one(tmp_path):
    db = connect(tmp_path / "paper.sqlite")
    result = process(db, message(4, "Text: Sell SPX 7690C/P, Buy 7670P and buy 7710C and collect $850 for today"))
    assert result == {"action": "opened", "message_id": 4, "symbol": "SPX", "legs": 4, "quantity": 1, "entry_price": 8.5}
    row = db.execute("select legs_json,quantity,take_profit_pct,stop_loss_pct from positions").fetchone()
    assert len(__import__('json').loads(row['legs_json'])) == 4
    assert (row['quantity'], row['take_profit_pct'], row['stop_loss_pct']) == (1, 50, 25)


def test_opens_once_and_closes_on_matching_signal(tmp_path):
    db = connect(tmp_path / "paper.sqlite")
    opened = process(db, message(10, "Text: Sell SPX 7635C n Buy 7645C and collect $500 for today"))
    assert opened["action"] == "opened"
    assert process(db, message(10, "same"))["action"] == "duplicate"
    closed = process(db, message(11, "Text: 15% on 7635/7645C, Take gains"))
    assert closed["action"] == "exit_pending" and closed["reason"] == "exit_price_unavailable"
    assert snapshot(db)["open"] == 1
    closed = process(db, message(12, "SPX 7635C qty 1\nPnL (live estimate): +75.00"))
    assert closed['action'] == 'closed' and closed['symbol'] == 'SPX'
    assert snapshot(db)["closed"] == 1


def test_pnl_thresholds_and_ambiguous_close_fail_closed(tmp_path):
    db = connect(tmp_path / "paper.sqlite")
    process(db, message(20, "Text: IWM 295C at $0.50 for today"))
    assert process(db, message(21, "#4 IWM 295.0CE qty 1\nPnL (live estimate): +14.00"))["action"] == "marked"
    assert process(db, message(22, "#4 IWM 295.0CE qty 1\nPnL (live estimate): -13.00"))["reason"] == "sl_hit"
    process(db, message(30, "Text: QQQ 721C at $0.50 for today"))
    process(db, message(31, "Text: QQQ 723C at $0.40 for today"))
    assert process(db, message(32, "Text: Take gains on QQQ calls"))["action"] == "blocked"


def test_incomplete_or_unbounded_text_does_not_open(tmp_path):
    db = connect(tmp_path / "paper.sqlite")
    result = process(db, message(40, "🛑 Blocked by trade quality check\nText: Sell IONQ 40C and collect premiums for 9/11, Convert Call to bear call"))
    assert result["action"] == "blocked"
    assert snapshot(db)["open"] == 0


def test_block_preserves_human_reason(tmp_path):
    db = connect(tmp_path / "paper.sqlite")
    result = process(db, message(41, "ℹ️ Market update\nReasoning: Missing direction and entry price.\nText: $MU 1020 CALL"))
    assert result["upstream_reason"] == "Missing direction and entry price."


def test_admin_messages_cannot_close_even_one_position(tmp_path):
    db = connect(tmp_path / 'paper.sqlite')
    process(db, message(1, 'Text: QQQ 721C at $0.50 for today'))
    for mid, text in enumerate(['Commands', '📊 Status', '📊 EOD Report — 2026-09-09', "📊 This month\'s PnL", '📊 All-time PnL', '💤'], 2):
        result = process(db, message(mid, text + '\nPnL: +1000.00\nclose positions'))
        assert result['action'] == 'ignored'
    assert snapshot(db)['open'] == 1


def test_unidentified_close_never_uses_only_open_position(tmp_path):
    db = connect(tmp_path / 'paper.sqlite')
    process(db, message(1, 'Text: QQQ 721C at $0.50 for today'))
    assert process(db, message(2, 'Text: Trim 80%'))['action'] == 'blocked'
    assert snapshot(db)['open'] == 1


def test_notifications_are_durable_and_do_not_backfill_duplicates(tmp_path):
    path = tmp_path / 'paper.sqlite'
    db = connect(path)
    process(db, message(1, 'Text: QQQ 721C at $0.50 for today'))
    process(db, message(2, 'Commands'))
    db.close()
    db = connect(path)
    assert len(db.execute('select * from notification_outbox where delivered_at is null').fetchall()) == 1
    assert process(db, message(1, 'duplicate'))['action'] == 'duplicate'
    assert db.execute('select count(*) from notification_outbox').fetchone()[0] == 1


def test_expiration_year_and_exchange_date_are_preserved():
    entry = parse_entry(1, '2026-09-10T01:00:00+00:00', 'Text: SPX 7685C at $2.05 for today')
    assert entry['expiration'] == '2026-09-09'
    entry = parse_entry(2, message(2, '')['date'], 'Text: SPX 7685C at $2.05 for 8/19/2027')
    assert entry['expiration'] == '2027-08-19'
    assert parse_entry(3, message(3, '')['date'], 'Text: SPX 7685C at $2.05 for 13/32') is None


def test_wrong_option_kind_or_partial_spread_cannot_close(tmp_path):
    db = connect(tmp_path / 'paper.sqlite')
    process(db, message(1, 'Text: Sell SPX 7635C n Buy 7645C and collect $500 for today'))
    for mid, text in enumerate(['Close SPX 7635P', 'Close SPX 7635/7650C', 'Close SPX 7635C 2026-09-10'], 2):
        assert process(db, message(mid, text))['action'] == 'blocked'
    assert snapshot(db)['open'] == 1


def test_image_only_message_is_preserved_for_review_without_trade_or_alert(tmp_path):
    db = connect(tmp_path / 'paper.sqlite')
    result = process(db, {**message(1, 'Text: (none)'), 'media_path': '/local/1.jpg'})
    assert result['action'] == 'needs_review'
    assert snapshot(db)['images_needing_review'] == 1
    assert db.execute('select media_path from attachments').fetchone()[0] == '/local/1.jpg'
    assert db.execute('select count(*) from notification_outbox').fetchone()[0] == 0
    assert snapshot(db)['open'] == 0
