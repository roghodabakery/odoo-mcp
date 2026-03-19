from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
    )

    odoo_url: str
    odoo_api_key: str
    odoo_user: str


settings = Settings()
