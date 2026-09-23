from types import SimpleNamespace

from pydantic import SecretStr

from app import config
from app.config import Settings


def test_local_constants_override_environment(monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "local_constants",
        SimpleNamespace(
            TELEGRAM_BOT_TOKEN="constant-bot-token",
            TELEGRAM_ALLOWED_USER_ID=123456789,
            FUNDING_NODE_URL="https://constant-node.invalid",
            FUNDING_NODE_API_KEY="constant-api-key",
            FUNDING_MASTER_PRIVATE_KEY="01".rjust(64, "0"),
            FUNDING_MASTER_ADDRESS="TMVQGm1qAQYVdetCeGRRkTWYYrLXuHK2HC",
            TELEGRAM_FUNDING_ENABLED=True,
        ),
    )
    monkeypatch.setenv("TRONFORGE_TELEGRAM_BOT_TOKEN", "environment-bot-token")
    monkeypatch.setenv("TRONFORGE_TELEGRAM_ALLOWED_USER_ID", "987654321")
    monkeypatch.setenv("TRONFORGE_FUNDING_NODE_URL", "https://environment-node.invalid")

    settings = Settings(_env_file=None)

    assert settings.telegram_bot_token == SecretStr("constant-bot-token")
    assert settings.telegram_allowed_user_id == 123456789
    assert settings.funding_node_url == "https://constant-node.invalid"
    assert settings.funding_node_api_key == SecretStr("constant-api-key")
    assert settings.funding_master_private_key == SecretStr("01".rjust(64, "0"))
    assert settings.funding_master_address == "TMVQGm1qAQYVdetCeGRRkTWYYrLXuHK2HC"
    assert settings.telegram_funding_enabled is True


def test_explicit_settings_still_override_local_constants(monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "local_constants",
        SimpleNamespace(FUNDING_NODE_URL="https://constant-node.invalid"),
    )

    settings = Settings(_env_file=None, funding_node_url="https://test-node.invalid")

    assert settings.funding_node_url == "https://test-node.invalid"
