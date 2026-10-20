from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://fishingnet@127.0.0.1:55432/fishingnet'
    setup_token: str = ''
    master_key: str = ''
    app_origin: str = 'http://127.0.0.1:5173'
    cookie_secure: bool = False


settings = Settings()
