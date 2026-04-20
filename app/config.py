# config.py
import os

# Seguridad y sesión
SECRET_KEY = os.getenv("SECRET_KEY", "dev_secret_change_me")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "incidencias_session")

# Base de datos (MySQL / MariaDB)
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")  # en Docker Compose: 'db'; en local: '127.0.0.1'
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "db_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "db_user_pass")
DB_NAME = os.getenv("DB_NAME", "app_db")

# Pool settings
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", 1))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", 10))

# Construcción de DSN para aiomysql
DB_DSN = {
    "host": DB_HOST,
    "port": DB_PORT,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "db": DB_NAME,
    "autocommit": True,
}
