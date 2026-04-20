# main.py
import asyncio
import aiomysql
import logging
from typing import Optional, List, Dict
import config

logger = logging.getLogger("incidencias.main")
logger.setLevel(logging.INFO)

ROLE_REPORTER = "reporter"       # crea incidencias y ve solo las suyas
ROLE_TECHNICIAN = "technician"   # ve incidencias (con filtros)
ROLE_GROUP_ADMIN = "group_admin" # administra incidencias/usuarios de su grupo
ROLE_SUPERADMIN = "superadmin"   # administración global

USER_ROLES = {ROLE_REPORTER, ROLE_TECHNICIAN, ROLE_GROUP_ADMIN, ROLE_SUPERADMIN}

ROLE_ALIASES = {
    "creador": ROLE_REPORTER,
    "reporter": ROLE_REPORTER,
    "usuario": ROLE_REPORTER,
    "user": ROLE_REPORTER,
    "tecnico": ROLE_TECHNICIAN,
    "técnico": ROLE_TECHNICIAN,
    "technician": ROLE_TECHNICIAN,
    "tech": ROLE_TECHNICIAN,
    "admin_grupo": ROLE_GROUP_ADMIN,
    "group_admin": ROLE_GROUP_ADMIN,
    "grupo_admin": ROLE_GROUP_ADMIN,
    "admin": ROLE_SUPERADMIN,
    "superadmin": ROLE_SUPERADMIN,
}

def normalize_role(role: str) -> str:
    role = (role or "").strip().lower()
    role = ROLE_ALIASES.get(role, role)
    return role if role in USER_ROLES else ROLE_REPORTER


def hash_password(password: str) -> str:
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, password_hash: str) -> bool:
    import bcrypt

    return bcrypt.checkpw(password.encode("utf-8"), str(password_hash).encode("utf-8"))


db_pool: Optional[aiomysql.Pool] = None


async def close_db_pool() -> None:
    global db_pool
    if db_pool is None:
        return
    db_pool.close()
    await db_pool.wait_closed()
    db_pool = None

async def init_db_pool(loop):
    """
    Inicializa el pool de conexiones y crea tablas básicas si no existen.
    Llamar desde @app.before_server_start.
    """
    global db_pool
    dsn = config.DB_DSN.copy()
    # Reintentos simples para esperar a que MySQL esté listo
    retries = 8
    delay = 1
    for attempt in range(1, retries + 1):
        try:
            db_pool = await aiomysql.create_pool(
                host=dsn["host"],
                port=dsn["port"],
                user=dsn["user"],
                password=dsn["password"],
                db=dsn["db"],
                autocommit=dsn.get("autocommit", True),
                minsize=config.DB_POOL_MIN_SIZE,
                maxsize=config.DB_POOL_MAX_SIZE,
                loop=loop,
            )
            logger.info("Conectado a la base de datos.")
            break
        except Exception as e:
            logger.warning("Intento %s: no se pudo conectar a la DB: %s", attempt, e)
            if attempt == retries:
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5)

    # Crear tablas si no existen
    await _create_tables()

async def _create_tables():
    """
    Crea tablas mínimas para que la app funcione en un entorno nuevo.
    """
    global db_pool
    if db_pool is None:
        raise RuntimeError("Pool de DB no inicializado")

    create_users = """
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(100) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(20) NOT NULL DEFAULT 'reporter',
        grupo_id INT NULL,
        is_admin TINYINT(1) DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    create_grupos = """
    CREATE TABLE IF NOT EXISTS grupos (
        id INT AUTO_INCREMENT PRIMARY KEY,
        nombre VARCHAR(150) NOT NULL,
        descripcion TEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    create_incidencias = """
    CREATE TABLE IF NOT EXISTS incidencias (
        id INT AUTO_INCREMENT PRIMARY KEY,
        titulo VARCHAR(255) NOT NULL,
        descripcion TEXT,
        prioridad VARCHAR(50),
        estado VARCHAR(50) DEFAULT 'abierta',
        creador_id INT,
        asignado_id INT,
        grupo_id INT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(create_users)
            await cur.execute(create_grupos)
            await cur.execute(create_incidencias)

            # Migraciones ligeras (para DB ya existente)
            await _migrate_schema(cur)

            # Insertar / asegurar un usuario admin por defecto
            await cur.execute("SELECT COUNT(*) FROM users;")
            (count,) = await cur.fetchone()
            if count == 0:
                # password por defecto: 'admin'
                pw_hash = hash_password("admin")
                await cur.execute(
                    "INSERT INTO users (username, password_hash, role, is_admin) VALUES (%s, %s, %s, %s)",
                    ("admin", pw_hash, ROLE_SUPERADMIN, 1),
                )
            else:
                # Si el usuario admin ya existe, asegúrate de que sea superadmin
                await cur.execute(
                    "UPDATE users SET role=%s, is_admin=1 WHERE username='admin' OR is_admin=1",
                    (ROLE_SUPERADMIN,),
                )
    logger.info("Tablas creadas / verificadas y usuario admin por defecto preparado.")


async def _column_exists(cur, table: str, column: str) -> bool:
    await cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    return (await cur.fetchone()) is not None


async def _migrate_schema(cur) -> None:
    """Pequeñas migraciones para no romper cuando ya existe la DB."""
    # users.role
    if not await _column_exists(cur, "users", "role"):
        await cur.execute(
            "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'reporter'"
        )

    # users.grupo_id
    if not await _column_exists(cur, "users", "grupo_id"):
        await cur.execute("ALTER TABLE users ADD COLUMN grupo_id INT NULL")


# ----------------- Funciones de la app -----------------

async def verify_user(username: str, password: str) -> Optional[Dict]:
    """
    Verifica credenciales. Devuelve dict con user si OK, o None.
    """
    global db_pool
    if not username or not password:
        return None
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, username, password_hash, is_admin, role, grupo_id FROM users WHERE username=%s",
                (username,),
            )
            user = await cur.fetchone()
            if not user:
                return None

            role = normalize_role(
                user.get("role")
                or (ROLE_SUPERADMIN if user.get("is_admin") else ROLE_REPORTER)
            )
            is_admin = bool(user.get("is_admin")) or role == ROLE_SUPERADMIN

            try:
                stored_hash = user.get("password_hash")
                if not stored_hash:
                    return None

                if check_password(password, stored_hash):
                    return {
                        "id": user["id"],
                        "username": user["username"],
                        "role": role,
                        "grupo_id": user.get("grupo_id"),
                        "is_admin": is_admin,
                    }
            except Exception:
                return None
    return None

INCIDENCIAS_SELECT = """
SELECT
    i.id,
    i.titulo,
    i.descripcion,
    i.prioridad,
    i.estado,
    i.creador_id,
    i.asignado_id,
    i.grupo_id,
    i.created_at,
    g.nombre AS grupo_nombre,
    (
        SELECT u.username
        FROM users u
        WHERE u.role='group_admin' AND u.grupo_id=i.grupo_id
        ORDER BY u.id
        LIMIT 1
    ) AS grupo_admin_username,
    creador.username AS creador_username,
    asignado.username AS asignado_username
FROM incidencias i
LEFT JOIN grupos g ON i.grupo_id = g.id
LEFT JOIN users creador ON i.creador_id = creador.id
LEFT JOIN users asignado ON i.asignado_id = asignado.id
"""


async def create_user(
    username: str,
    password: str,
    role: str = ROLE_REPORTER,
    grupo_id: Optional[int] = None,
) -> int:
    """Crea un usuario. Si role es superadmin, también marca is_admin=1."""
    global db_pool
    if not username or not password:
        raise ValueError("username y password son obligatorios")

    role = normalize_role(role)
    pw_hash = hash_password(password)
    is_admin = 1 if role == ROLE_SUPERADMIN else 0

    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO users (username, password_hash, role, grupo_id, is_admin) VALUES (%s,%s,%s,%s,%s)",
                (username, pw_hash, role, grupo_id, is_admin),
            )
            return int(cur.lastrowid)


async def get_user_by_username(username: str) -> Optional[Dict]:
    global db_pool
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, username, role, grupo_id, is_admin FROM users WHERE username=%s",
                (username,),
            )
            row = await cur.fetchone()
            if not row:
                return None
            role = normalize_role(
                row.get("role") or (ROLE_SUPERADMIN if row.get("is_admin") else ROLE_REPORTER)
            )
            row["role"] = role
            row["is_admin"] = bool(row.get("is_admin")) or role == ROLE_SUPERADMIN
            return row


async def list_users() -> List[Dict]:
    global db_pool
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, username, role, grupo_id, is_admin FROM users ORDER BY username"
            )
            rows = await cur.fetchall() or []
            for r in rows:
                role = normalize_role(
                    r.get("role")
                    or (ROLE_SUPERADMIN if r.get("is_admin") else ROLE_REPORTER)
                )
                r["role"] = role
                r["is_admin"] = bool(r.get("is_admin")) or role == ROLE_SUPERADMIN
            return rows


async def set_user_role(user_id: int, role: str) -> None:
    global db_pool
    role = normalize_role(role)
    is_admin = 1 if role == ROLE_SUPERADMIN else 0
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET role=%s, is_admin=%s WHERE id=%s",
                (role, is_admin, user_id),
            )


async def set_user_group(user_id: int, grupo_id: Optional[int]) -> None:
    global db_pool
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET grupo_id=%s WHERE id=%s",
                (grupo_id, user_id),
            )


async def get_group_admin_for_group(grupo_id: int) -> Optional[Dict]:
    global db_pool
    if not grupo_id:
        return None
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id, username FROM users WHERE role=%s AND grupo_id=%s ORDER BY id LIMIT 1",
                (ROLE_GROUP_ADMIN, grupo_id),
            )
            return await cur.fetchone()


async def get_technicians_by_group(grupo_id: int) -> List[Dict]:
    global db_pool
    if not grupo_id:
        return []
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT id, username
                FROM users
                WHERE grupo_id=%s AND role IN (%s, %s)
                ORDER BY username
                """,
                (grupo_id, ROLE_TECHNICIAN, ROLE_GROUP_ADMIN),
            )
            return await cur.fetchall() or []


async def get_all_incidencias() -> List[Dict]:
    """Compatibilidad: devuelve todas las incidencias (útil para admin)."""
    return await get_incidencias_for_user(
        {"id": 0, "role": ROLE_SUPERADMIN, "grupo_id": None},
        scope="all",
    )


async def get_incidencias_for_user(user: Dict, scope: str = "all") -> List[Dict]:
    global db_pool

    role = normalize_role(user.get("role"))
    user_id = user.get("id")
    grupo_id = user.get("grupo_id")

    scope = (scope or "all").strip().lower()

    conditions: List[str] = []
    params: List = []

    if role == ROLE_REPORTER:
        conditions.append("i.creador_id=%s")
        params.append(user_id)
    else:
        # Los admins de grupo solo pueden ver incidencias de su grupo
        if role == ROLE_GROUP_ADMIN:
            if not grupo_id:
                return []
            conditions.append("i.grupo_id=%s")
            params.append(grupo_id)

        if scope == "mine":
            conditions.append("i.asignado_id=%s")
            params.append(user_id)
        elif scope == "group":
            if not grupo_id:
                return []
            if role != ROLE_GROUP_ADMIN:
                conditions.append("i.grupo_id=%s")
                params.append(grupo_id)
        elif scope == "created":
            conditions.append("i.creador_id=%s")
            params.append(user_id)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    query = INCIDENCIAS_SELECT
    if where:
        query += "\n" + where
    query += "\nORDER BY i.created_at DESC"

    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(query, params)
            return await cur.fetchall() or []


async def create_incidencia(
    titulo: str,
    descripcion: str,
    prioridad: str,
    creador_id: int,
    grupo_id: Optional[int] = None,
) -> None:
    global db_pool
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO incidencias (titulo, descripcion, prioridad, creador_id, grupo_id) VALUES (%s,%s,%s,%s,%s)",
                (titulo, descripcion, prioridad, creador_id, grupo_id),
            )


async def get_incidencia_by_id(incidencia_id: int) -> Optional[Dict]:
    global db_pool
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(INCIDENCIAS_SELECT + "\nWHERE i.id=%s", (incidencia_id,))
            return await cur.fetchone()


async def update_incidencia(
    incidencia_id: int,
    titulo: str,
    descripcion: str,
    estado: str,
    prioridad: str,
    asignado_id: Optional[int],
    grupo_id: Optional[int],
) -> None:
    global db_pool
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE incidencias SET titulo=%s, descripcion=%s, estado=%s, prioridad=%s, asignado_id=%s, grupo_id=%s WHERE id=%s",
                (titulo, descripcion, estado, prioridad, asignado_id, grupo_id, incidencia_id),
            )


async def get_all_grupos() -> List[Dict]:
    global db_pool
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM grupos ORDER BY nombre")
            return await cur.fetchall() or []


async def get_grupo_by_nombre(nombre: str) -> Optional[Dict]:
    global db_pool
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM grupos WHERE nombre=%s", (nombre,))
            return await cur.fetchone()


async def create_grupo(nombre: str, descripcion: str = "") -> int:
    global db_pool
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO grupos (nombre, descripcion) VALUES (%s,%s)",
                (nombre, descripcion),
            )
            return int(cur.lastrowid)


async def assign_user_to_group(usuario_id: int, grupo_id: int, rol: str = "technician") -> None:
    """Asigna un usuario a un grupo (users.grupo_id) y opcionalmente ajusta el role."""
    global db_pool

    raw = (rol or "").strip().lower()
    if raw in {"admin", "administrador", "group_admin", "admin_grupo"}:
        role = ROLE_GROUP_ADMIN
    elif raw in {"miembro", "member", "technician", "tecnico", "técnico"}:
        role = ROLE_TECHNICIAN
    elif raw in {"reporter", "creador", "usuario"}:
        role = ROLE_REPORTER
    else:
        role = normalize_role(raw)

    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET grupo_id=%s, role=%s WHERE id=%s AND is_admin=0",
                (grupo_id, role, usuario_id),
            )
