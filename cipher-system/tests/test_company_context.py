from datetime import date
from core import company_context


def test_sec_extractors_keep_filing_provenance():
    facts = {"facts": {"us-gaap": {"Revenues": {"label": "Revenue", "units": {"USD": [
        {"form": "10-Q", "filed": "2026-05-01", "end": "2026-03-31", "val": 10, "accn": "x"},
        {"form": "10-Q", "filed": "2026-08-01", "end": "2026-06-30", "val": 12, "accn": "y"}]}}}}}
    row = company_context.extract_fundamentals(facts)[0]
    assert row["val"] == 12 and row["filed"] == "2026-08-01" and row["tag"] == "Revenues"


def test_bls_ics_events_are_windowed_and_sourced():
    raw = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20260820T083000\r\nSUMMARY:Consumer Price Index\r\nEND:VEVENT\r\nEND:VCALENDAR"
    rows = company_context.parse_ics(raw, start=date(2026, 8, 14), days=10)
    assert rows[0]["date"] == "2026-08-20"
    assert rows[0]["source_url"].startswith("https://www.bls.gov/")


def test_filing_extractor_tolerates_incomplete_parallel_sec_arrays():
    submissions = {"cik": "0000012345", "filings": {"recent": {
        "accessionNumber": ["0001-02-000003"], "form": ["8-K"], "primaryDocument": [],
    }}}
    rows = company_context.extract_filings(submissions)
    assert rows[0]["form"] == "8-K"
    assert rows[0]["filed"] is None
    assert rows[0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/12345/")
