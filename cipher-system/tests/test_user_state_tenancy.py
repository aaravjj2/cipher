from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "0001_user_state.sql"
MIGRATION_3 = ROOT / "supabase" / "migrations" / "0003_chart_saves_and_standing_notes.sql"


USER_TABLES = (
    "user_profiles",
    "watchlists",
    "watchlist_members",
    "saved_screens",
    "journal_entries",
    "chart_templates",
    "workspace_layouts",
    "holdings",
    "alerts",
    "portfolio_risk_positions",
)


def test_user_tables_have_user_id_and_rls():
    sql = MIGRATION.read_text(encoding="utf-8")

    for table in USER_TABLES:
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert f"user_id uuid not null references auth.users(id)" in sql


def test_schema_has_no_raw_provider_secret_columns():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "alpaca_api_key" not in sql
    assert "alpaca_secret" not in sql
    assert "private_key" not in sql


def test_saved_screens_cannot_reference_another_users_watchlist():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "foreign key (watchlist_id, user_id)" in sql
    assert "references public.watchlists(id, user_id)" in sql


def test_late_user_owned_surfaces_have_rls_and_no_secret_columns():
    sql = MIGRATION_3.read_text(encoding="utf-8").lower()
    for table in ("chart_saves", "standing_notes"):
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert "user_id uuid not null references auth.users(id)" in sql
    assert "alpaca" not in sql
    assert "auth.uid()" in sql


def test_policies_bind_to_auth_uid():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.count("auth.uid()") >= 12
    assert "using (user_id = auth.uid())" in sql
    assert "with check (user_id = auth.uid())" in sql
