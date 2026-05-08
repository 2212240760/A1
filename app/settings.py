from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/app.db"
    data_dir: str = "./data"
    session_secret: str = "dev-session-secret"
    default_admin_username: str = "admin"
    default_admin_password: str = "admin"


settings = Settings()
