from pathlib import Path
import re


def _sql_files() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
    return sorted(root.glob("*.sql"))


def test_new_migrations_are_additive():
    files = _sql_files()
    assert files, "expected supabase/migrations SQL files"
    for path in files:
        sql = path.read_text().lower()
        assert "drop table" not in sql
        assert "drop column" not in sql
        assert "truncate " not in sql
        assert "delete from" not in sql
        assert re.search(r"alter table\s+\S+\s+drop\b", sql) is None
        # DROP TRIGGER / DROP POLICY / ENABLE ROW LEVEL SECURITY are allowed.


def test_scoring_sql_never_reads_ai_proposed_value():
    core = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "20260919120000_survey_longitudinal_core.sql"
    ).read_text()
    function = core.split("CREATE OR REPLACE FUNCTION public.score_survey_instance", 1)[1]
    function_body = function.split("COMMENT ON FUNCTION public.score_survey_instance", 1)[0]
    assert "confirmed_value" in function_body
    assert "r.ai_proposed_value" not in function_body
    assert "select ai_proposed_value" not in function_body.lower()
