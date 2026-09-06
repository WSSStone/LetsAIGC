from typer.testing import CliRunner

from letsaigc.cli import app


def test_review_cli_has_no_execution_or_arbitrary_bind_arguments():
    result = CliRunner().invoke(app, ["ui", "review", "--help"])
    assert result.exit_code == 0
    assert "--open" in result.output and "--no-open" in result.output
    assert "--host" not in result.output and "--approve" not in result.output
