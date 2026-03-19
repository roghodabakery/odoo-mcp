from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    odoo_url: str
    odoo_db: str
    odoo_user: str
    odoo_api_key: str
    mcp_server_token: str = ""  # optional Bearer token for remote auth

settings = Settings()
