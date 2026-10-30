from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    mongodb_uri: str = "mongodb://localhost:27017/?replicaSet=rs0"
    mongodb_database: str = "sjms"
    legacy_postgres_url: str = ""
    jwt_secret: str = ""
    access_token_minutes: int = 60
    cors_origins: str = "http://localhost:5173"
    metals_dev_api_key: str = ""
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

settings = Settings()

