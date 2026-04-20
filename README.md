# repositorio

Copia de `Ssanic` adaptada para ejecutarse en local.

## Requisitos
- Python 3.11+
- Docker Desktop (recomendado) para levantar MySQL + phpMyAdmin

## Arranque (local)
0) Asegúrate de tener Docker Desktop abierto/ejecutándose.

1) Levanta la base de datos (desde `app/`):

```powershell
cd .\app
docker compose up -d
```

- phpMyAdmin: http://localhost:8082
- MySQL: `127.0.0.1:3306` (DB: `app_db`, user: `db_user`, pass: `db_user_pass`)

2) Crea un entorno virtual e instala dependencias:

Recomendado en Windows (para usar una versión compatible de Python):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -r .\requirements.txt
```

3) Ejecuta la app:

```powershell
$env:DB_HOST = "127.0.0.1"
.\.venv\Scripts\python .\app\server.py
```

- App: http://127.0.0.1:8000
- Login: `admin` / `admin`

Nota: en el primer arranque la app crea las tablas necesarias y prepara el usuario `admin`.

Opcional (borra datos): reiniciar la base de datos desde cero

```powershell
cd .\app
docker compose down -v
docker compose up -d
```

4) Parar servicios

- Parar servidor: `Ctrl+C` en la terminal donde lo ejecutaste.
- Parar MySQL/phpMyAdmin:

```powershell
cd .\app
docker compose down
```

## Variables de entorno
- DB: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- App: `APP_HOST`, `APP_PORT`, `APP_DEBUG`, `APP_AUTO_RELOAD`

## Opción: todo en Docker
Si prefieres levantar también la app en contenedor:

```powershell
cd .\app
docker compose -f .\compose.docker.yml up -d --build
```
