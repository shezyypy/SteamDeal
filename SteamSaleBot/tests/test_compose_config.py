from pathlib import Path


def test_compose_blocks_database_migrations_until_preflight_passes():
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    assert "  preflight:" in compose
    assert "command: python scripts/preflight.py" in compose
    migrate = compose.split("  migrate:", 1)[1].split("  bot:", 1)[0]
    assert "preflight:" in migrate
    assert "condition: service_completed_successfully" in migrate
