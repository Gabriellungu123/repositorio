#!/usr/bin/env python
import argparse
import asyncio
import datetime
import getpass
import os
import random
import re
import secrets
import string
import sys
import unicodedata

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import main  # noqa: E402


def _print_users(rows):
    if not rows:
        print("(sin usuarios)")
        return

    print("ID\tUSERNAME\tROLE\tGRUPO_ID\tIS_ADMIN")
    for r in rows:
        print(
            f"{r.get('id')}\t{r.get('username')}\t{r.get('role')}\t{r.get('grupo_id')}\t{int(bool(r.get('is_admin')))}"
        )


def _resolve_role(role: str) -> str:
    return main.normalize_role(role)


def _generate_password(length: int = 14) -> str:
    # bcrypt has a 72-byte limit; keep it short and simple
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _slugify_username(full_name: str) -> str:
    """Convierte 'Nombre Apellido' -> 'nombre.apellido' (ASCII, sin acentos)."""

    name = (full_name or "").strip().lower()
    # quita acentos
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))

    name = name.replace("'", "")
    name = re.sub(r"\s+", ".", name)
    name = re.sub(r"[^a-z0-9\.]", "", name)
    name = re.sub(r"\.+", ".", name).strip(".")
    return name or "user"


async def _available_username(base: str, reserved: set[str]) -> str:
    base = (base or "user").strip().lower()
    base = re.sub(r"[^a-z0-9\.]", "", base) or "user"

    if base not in reserved and not await main.get_user_by_username(base):
        reserved.add(base)
        return base

    i = 2
    while True:
        cand = f"{base}{i}"
        if cand not in reserved and not await main.get_user_by_username(cand):
            reserved.add(cand)
            return cand
        i += 1


async def _ensure_user_with_password(
    username: str,
    role: str,
    grupo_id: int | None,
    password: str,
) -> dict:
    """Crea/actualiza un usuario y fuerza password simple."""

    role = main.normalize_role(role)
    existing = await main.get_user_by_username(username)

    if existing:
        uid = int(existing["id"])
        await main.set_user_role(uid, role)
        await main.set_user_group(uid, grupo_id)

        # fuerza password simple
        pw_hash = main.hash_password(password)
        async with main.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE users SET password_hash=%s WHERE id=%s",
                    (pw_hash, uid),
                )

        return await main.get_user_by_username(username) or existing

    await main.create_user(username, password, role=role, grupo_id=grupo_id)
    return await main.get_user_by_username(username) or {
        "username": username,
        "role": role,
        "grupo_id": grupo_id,
    }


async def _create_incidencia_full(
    *,
    titulo: str,
    descripcion: str,
    prioridad: str,
    estado: str,
    creador_id: int,
    grupo_id: int,
    asignado_id: int | None,
) -> int:
    async with main.db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO incidencias
                    (titulo, descripcion, prioridad, estado, creador_id, asignado_id, grupo_id)
                VALUES
                    (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    titulo,
                    descripcion,
                    prioridad,
                    estado,
                    creador_id,
                    asignado_id,
                    grupo_id,
                ),
            )
            return int(cur.lastrowid)


async def _ensure_group(nombre: str, descripcion: str = "") -> int:
    existing = await main.get_grupo_by_nombre(nombre)
    if existing:
        return int(existing["id"])
    return await main.create_grupo(nombre, descripcion)


async def _ensure_user(
    username: str,
    role: str,
    grupo_id: int | None,
) -> tuple[dict, str | None, bool]:
    """Crea usuario si no existe.

    Devuelve (user_row, password_if_created, created_bool).
    Si ya existe, se ajusta role/grupo y NO se cambia la contraseña.
    """

    role = main.normalize_role(role)

    existing = await main.get_user_by_username(username)
    if existing:
        await main.set_user_role(int(existing["id"]), role)
        await main.set_user_group(int(existing["id"]), grupo_id)
        updated = await main.get_user_by_username(username) or existing
        return updated, None, False

    password = _generate_password()
    await main.create_user(username, password, role=role, grupo_id=grupo_id)
    created = await main.get_user_by_username(username) or {
        "username": username,
        "role": role,
        "grupo_id": grupo_id,
    }
    return created, password, True


async def _seed_demo(output_path: str) -> str:
    """Crea 5 grupos y usuarios demo y genera un TXT con el resultado."""

    # Si es relativo, escribe en la carpeta de la app
    if not os.path.isabs(output_path):
        output_path = os.path.join(BASE_DIR, output_path)

    groups = [
        ("Informatica", "Soporte IT y sistemas", "inf"),
        ("Recursos Humanos / Administración", "RRHH y administración", "rrhh"),
        ("Escuela", "Aulas y escuela", "esc"),
        ("Limpieza", "Servicios de limpieza", "limp"),
        ("Mantenimiento", "Mantenimiento y reparaciones", "mant"),
    ]

    # seed
    seeded = []
    for nombre, descripcion, code in groups:
        gid = await _ensure_group(nombre, descripcion)

        # Superior (admin de grupo)
        admin_username = f"{code}_admin"
        admin_user, admin_pw, admin_created = await _ensure_user(
            admin_username, main.ROLE_GROUP_ADMIN, gid
        )

        # Técnicos
        techs = []
        for i in range(1, 3):
            uname = f"{code}_tec{i}"
            u, pw, created = await _ensure_user(uname, main.ROLE_TECHNICIAN, gid)
            techs.append((u, pw, created))

        # Usuarios creadores (reporters)
        reporters = []
        for i in range(1, 3):
            uname = f"{code}_usr{i}"
            u, pw, created = await _ensure_user(uname, main.ROLE_REPORTER, gid)
            reporters.append((u, pw, created))

        seeded.append(
            {
                "nombre": nombre,
                "id": gid,
                "admin": (admin_user, admin_pw, admin_created),
                "techs": techs,
                "reporters": reporters,
            }
        )

    # TXT
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    lines = []
    lines.append("USUARIOS DEMO - Gestión de Incidencias (Sanic)")
    lines.append(f"Generado: {now}")
    lines.append("")
    lines.append("SUPERADMIN GLOBAL")
    lines.append("- admin (password por defecto: admin)")
    lines.append("")
    lines.append(
        "NOTA: Las contraseñas solo se muestran para los usuarios creados en esta ejecución."
    )
    lines.append("Si un usuario ya existía, su contraseña NO se cambió.")
    lines.append("")

    for g in seeded:
        lines.append(f"GRUPO: {g['nombre']} (id={g['id']})")

        admin_user, admin_pw, admin_created = g["admin"]
        lines.append("  Superior (admin de grupo):")
        if admin_created:
            lines.append(f"    - {admin_user['username']} / password: {admin_pw}")
        else:
            lines.append(f"    - {admin_user['username']} (ya existía)")

        lines.append("  Técnicos:")
        for u, pw, created in g["techs"]:
            if created:
                lines.append(f"    - {u['username']} / password: {pw}")
            else:
                lines.append(f"    - {u['username']} (ya existía)")

        lines.append("  Usuarios (creadores):")
        for u, pw, created in g["reporters"]:
            if created:
                lines.append(f"    - {u['username']} / password: {pw}")
            else:
                lines.append(f"    - {u['username']} (ya existía)")

        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

    return output_path


async def _seed_people(
    output_path: str,
    *,
    total_users: int = 50,
    incidents_per_group: int = 50,
    password: str = "1234",
) -> str:
    """Genera usuarios con nombres realistas + incidencias por grupo.

    - Crea/asegura 5 grupos.
    - Crea (por grupo): 1 admin de grupo + 2 técnicos (con usernames tipo nombre.apellido).
    - Crea N usuarios reporters (distribuidos entre los 5 grupos).
    - Crea X incidencias por grupo, con títulos/descripciones más realistas.

    Nota: El password indicado se fuerza (también si el usuario ya existía).
    """

    if not os.path.isabs(output_path):
        output_path = os.path.join(BASE_DIR, output_path)

    groups = [
        ("Informatica", "Soporte IT y sistemas", "INF"),
        ("Recursos Humanos / Administración", "RRHH y administración", "RRHH"),
        ("Escuela", "Aulas y escuela", "ESC"),
        ("Limpieza", "Servicios de limpieza", "LIMP"),
        ("Mantenimiento", "Mantenimiento y reparaciones", "MANT"),
    ]

    staff_names = {
        "INF": {
            "admin": "Carlos Moreno",
            "techs": ["Lucía Sánchez", "David Ruiz"],
        },
        "RRHH": {
            "admin": "María Pérez",
            "techs": ["Javier Martín", "Elena Gómez"],
        },
        "ESC": {
            "admin": "Sergio Ramírez",
            "techs": ["Laura Díaz", "Pablo Herrera"],
        },
        "LIMP": {
            "admin": "Rosa Navarro",
            "techs": ["Álvaro Torres", "Irene Campos"],
        },
        "MANT": {
            "admin": "Antonio Molina",
            "techs": ["Carmen Ortega", "Miguel Castillo"],
        },
    }

    first_names = [
        "Ana",
        "Luis",
        "Carmen",
        "Jorge",
        "María",
        "Pedro",
        "Lucía",
        "David",
        "Elena",
        "Sergio",
        "Laura",
        "Pablo",
        "Marta",
        "Diego",
        "Raquel",
        "Iván",
        "Irene",
        "Javier",
        "Claudia",
        "Hugo",
        "Nuria",
        "Adrián",
        "Silvia",
        "Víctor",
        "Patricia",
        "Álvaro",
        "Beatriz",
        "Daniel",
        "Cristina",
        "Rubén",
        "Teresa",
        "Samuel",
    ]

    last_names = [
        "García",
        "Martínez",
        "López",
        "Sánchez",
        "Pérez",
        "Gómez",
        "Fernández",
        "Díaz",
        "Ruiz",
        "Hernández",
        "Jiménez",
        "Moreno",
        "Muñoz",
        "Álvarez",
        "Romero",
        "Alonso",
        "Gutiérrez",
        "Navarro",
        "Torres",
        "Domínguez",
        "Vázquez",
        "Ramos",
        "Gil",
        "Serrano",
        "Blanco",
        "Molina",
        "Morales",
        "Ortega",
        "Delgado",
        "Castillo",
        "Cruz",
        "Flores",
        "Reyes",
        "Herrera",
        "Campos",
        "Ramírez",
    ]

    incident_titles = {
        "INF": [
            "No funciona el correo",
            "El ordenador va muy lento",
            "No puedo iniciar sesión",
            "Problema con la impresora",
            "WiFi sin conexión",
            "VPN no conecta",
            "Error al abrir una aplicación",
            "Pantalla en negro",
            "Teclado/ratón no responde",
            "Actualización bloqueada",
        ],
        "RRHH": [
            "Acceso al portal del empleado",
            "Error en la nómina",
            "Solicitud de certificado",
            "Cambio de datos personales",
            "Problema con firma digital",
            "No llega notificación de RRHH",
            "Alta/Baja de usuario en sistema",
            "Permisos insuficientes",
            "Incidencia con vacaciones",
            "Documento pendiente de validación",
        ],
        "ESC": [
            "Proyector no enciende",
            "Pizarra digital no funciona",
            "Aula sin internet",
            "Altavoces sin sonido",
            "Ordenador del aula bloqueado",
            "Fallo al imprimir en el aula",
            "Problema con usuario de alumno",
            "Equipo no detecta HDMI",
            "Corte de red en aula",
            "Aplicación educativa no carga",
        ],
        "LIMP": [
            "Falta material de limpieza",
            "Derrame en pasillo",
            "Contenedor lleno",
            "Baño sin papel",
            "Zona con mal olor",
            "Cristales sucios",
            "Necesita limpieza urgente",
            "Falta jabón",
            "Suelo resbaladizo",
            "Basura acumulada",
        ],
        "MANT": [
            "Bombilla fundida",
            "Fuga de agua",
            "Puerta no cierra",
            "Aire acondicionado no funciona",
            "Calefacción no enciende",
            "Enchufe suelto",
            "Grieta en pared",
            "Silla/mesa rota",
            "Persiana atascada",
            "Problema con cerradura",
        ],
    }

    locations = [
        "Recepción",
        "Oficina 1",
        "Oficina 2",
        "Sala de reuniones",
        "Aula 101",
        "Aula 202",
        "Pasillo principal",
        "Baño planta baja",
        "Baño planta 1",
        "Almacén",
        "Taller",
    ]

    priorities = ["baja", "media", "alta"]
    priority_weights = [0.50, 0.35, 0.15]

    states = ["abierta", "en_proceso", "cerrada"]
    state_weights = [0.55, 0.30, 0.15]

    # Reserva para evitar colisiones de usernames durante la ejecución
    reserved: set[str] = set()

    # Crear grupos + staff
    seeded_groups = []
    for nombre, descripcion, code in groups:
        gid = await _ensure_group(nombre, descripcion)

        admin_name = staff_names[code]["admin"]
        admin_u = await _available_username(_slugify_username(admin_name), reserved)
        admin_user = await _ensure_user_with_password(
            admin_u, main.ROLE_GROUP_ADMIN, gid, password
        )

        tech_users = []
        for tech_name in staff_names[code]["techs"]:
            tech_u = await _available_username(_slugify_username(tech_name), reserved)
            tech_users.append(
                await _ensure_user_with_password(
                    tech_u, main.ROLE_TECHNICIAN, gid, password
                )
            )

        seeded_groups.append(
            {
                "nombre": nombre,
                "descripcion": descripcion,
                "code": code,
                "id": gid,
                "admin": admin_user,
                "techs": tech_users,
                "reporters": [],
                "incidents": 0,
            }
        )

    # Crear reporters
    total_users = int(total_users or 0)
    if total_users < 0:
        total_users = 0

    per_group = total_users // len(seeded_groups) if seeded_groups else total_users
    remainder = total_users % len(seeded_groups) if seeded_groups else 0

    used_full_names: set[str] = set()

    def _next_full_name() -> str:
        # Reintentos para asegurar unicidad
        for _ in range(2000):
            fn = random.choice(first_names)
            ln = random.choice(last_names)
            full = f"{fn} {ln}"
            if full not in used_full_names:
                used_full_names.add(full)
                return full
        # Fallback
        return f"User {len(used_full_names) + 1}"

    for idx, g in enumerate(seeded_groups):
        count = per_group + (1 if idx < remainder else 0)
        for _ in range(count):
            full_name = _next_full_name()
            uname = await _available_username(_slugify_username(full_name), reserved)
            reporter = await _ensure_user_with_password(
                uname, main.ROLE_REPORTER, int(g["id"]), password
            )
            g["reporters"].append(reporter)

    # Crear incidencias
    for g in seeded_groups:
        gid = int(g["id"])
        code = g["code"]

        reporters = g["reporters"]
        if not reporters:
            # fallback: usa el admin del grupo
            reporters = [g["admin"]]

        tech_ids = [int(t["id"]) for t in (g.get("techs") or []) if t.get("id")]

        title_pool = incident_titles.get(code) or ["Incidencia"]

        for i in range(1, int(incidents_per_group) + 1):
            creador = random.choice(reporters)
            creador_id = int(creador["id"])

            base_title = random.choice(title_pool)
            titulo = f"{base_title} ({code}-{i:02d})"

            prioridad = random.choices(priorities, weights=priority_weights, k=1)[0]
            estado = random.choices(states, weights=state_weights, k=1)[0]

            # Asignación a técnico con probabilidad (más alta si no está abierta)
            asignado_id = None
            if tech_ids:
                p_assign = 0.35 if estado == "abierta" else 0.85
                if random.random() < p_assign:
                    asignado_id = random.choice(tech_ids)

            ubicacion = random.choice(locations)
            descripcion = (
                f"Incidencia generada automáticamente para pruebas.\n"
                f"Ubicación: {ubicacion}.\n"
                f"Reportado por: {creador.get('username')}."
            )

            await _create_incidencia_full(
                titulo=titulo,
                descripcion=descripcion,
                prioridad=prioridad,
                estado=estado,
                creador_id=creador_id,
                asignado_id=asignado_id,
                grupo_id=gid,
            )

            g["incidents"] += 1

    # TXT
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    lines: list[str] = []
    lines.append("SEED PERSONAS - Gestión de Incidencias (Sanic)")
    lines.append(f"Generado: {now}")
    lines.append("")
    lines.append("CREDENCIALES")
    lines.append(f"- Password para TODOS los usuarios generados por este seed: {password}")
    lines.append("- Superadmin: admin / password: admin")
    lines.append("")

    total_reporters_created = sum(len(g["reporters"]) for g in seeded_groups)
    total_incidencias = sum(int(g["incidents"]) for g in seeded_groups)
    lines.append(
        f"Resumen: reporters={total_reporters_created} | incidencias_creadas={total_incidencias} ({incidents_per_group} por grupo)"
    )
    lines.append("")

    for g in seeded_groups:
        lines.append(f"GRUPO: {g['nombre']} (id={g['id']})")
        lines.append("  Admin de grupo:")
        lines.append(f"    - {g['admin']['username']}")
        lines.append("  Técnicos:")
        for t in g["techs"]:
            lines.append(f"    - {t['username']}")
        lines.append(f"  Usuarios (reporters) [{len(g['reporters'])}]:")
        for u in g["reporters"]:
            lines.append(f"    - {u['username']}")
        lines.append(f"  Incidencias creadas para este grupo: {g['incidents']}")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

    return output_path


def _read_passwords_from_seed_demo(path: str) -> dict[str, str]:
    """Lee usuarios_demo.txt (seed-demo) y devuelve username -> password."""

    if not path:
        return {}
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    if not os.path.exists(path):
        return {}

    pw: dict[str, str] = {}
    rx = re.compile(r"^\s*-\s*(\S+)\s*/\s*password:\s*(\S+)\s*$")
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = rx.match(line.rstrip("\n"))
            if m:
                pw[m.group(1)] = m.group(2)
    return pw


def _read_users_and_password_from_seed_people(path: str) -> tuple[set[str], str | None]:
    """Lee usuarios_personas.txt (seed-people).

    Devuelve (usernames, global_password_if_found).
    """

    if not path:
        return set(), None
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    if not os.path.exists(path):
        return set(), None

    usernames: set[str] = set()
    global_pw: str | None = None

    rx_pw = re.compile(r"Password para TODOS los usuarios generados por este seed:\s*(\S+)")
    rx_user = re.compile(r"^\s*-\s*([a-z0-9][a-z0-9\.]{1,62})\s*$", re.IGNORECASE)

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.rstrip("\n")

            m = rx_pw.search(line)
            if m:
                global_pw = m.group(1)
                continue

            m = rx_user.match(line)
            if m:
                usernames.add(m.group(1))

    return usernames, global_pw


async def _export_accounts(
    output_path: str,
    *,
    demo_file: str = "usuarios_demo.txt",
    people_file: str = "usuarios_personas.txt",
) -> str:
    """Genera un TXT con TODAS las cuentas (usuarios) actuales en la BD.

    Incluye passwords solo cuando se pueden inferir por seeds (usuarios_demo.txt / usuarios_personas.txt).
    """

    if not os.path.isabs(output_path):
        output_path = os.path.join(BASE_DIR, output_path)

    password_map: dict[str, str] = {"admin": "admin"}

    # Seed demo: passwords por-usuario
    password_map.update(_read_passwords_from_seed_demo(demo_file))

    # Seed people: password global para lista de usuarios
    people_users, people_pw = _read_users_and_password_from_seed_people(people_file)
    if people_pw:
        for u in people_users:
            password_map.setdefault(u, people_pw)

    # Leer usuarios + grupos
    async with main.db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, nombre FROM grupos ORDER BY nombre")
            grupos = await cur.fetchall() or []

        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.role,
                    u.grupo_id,
                    u.is_admin,
                    g.nombre AS grupo_nombre
                FROM users u
                LEFT JOIN grupos g ON u.grupo_id = g.id
                ORDER BY g.nombre, u.role, u.username
                """
            )
            users = await cur.fetchall() or []

    # Index groups
    group_order: list[tuple[int, str]] = [(int(g[0]), str(g[1])) for g in grupos]
    groups: dict[int, dict] = {
        gid: {
            "id": gid,
            "nombre": name,
            "admins": [],
            "techs": [],
            "reporters": [],
            "others": [],
        }
        for (gid, name) in group_order
    }

    superadmins: list[dict] = []
    no_group: list[dict] = []

    def _fmt_user(row) -> dict:
        role = main.normalize_role(row[2])
        is_admin = bool(row[4]) or role == main.ROLE_SUPERADMIN
        return {
            "id": int(row[0]),
            "username": str(row[1]),
            "role": role,
            "grupo_id": row[3],
            "grupo_nombre": row[5],
            "is_admin": is_admin,
            "password": password_map.get(str(row[1]), "(desconocida)"),
        }

    for r in users:
        u = _fmt_user(r)
        gid = u.get("grupo_id")

        if u["is_admin"]:
            superadmins.append(u)
            continue

        if not gid:
            no_group.append(u)
            continue

        gid_int = int(gid)
        if gid_int not in groups:
            groups[gid_int] = {
                "id": gid_int,
                "nombre": u.get("grupo_nombre") or f"Grupo {gid_int}",
                "admins": [],
                "techs": [],
                "reporters": [],
                "others": [],
            }

        if u["role"] == main.ROLE_GROUP_ADMIN:
            groups[gid_int]["admins"].append(u)
        elif u["role"] == main.ROLE_TECHNICIAN:
            groups[gid_int]["techs"].append(u)
        elif u["role"] == main.ROLE_REPORTER:
            groups[gid_int]["reporters"].append(u)
        else:
            groups[gid_int]["others"].append(u)

    # TXT
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    lines: list[str] = []
    lines.append("CUENTAS COMPLETAS - Gestión de Incidencias (Sanic)")
    lines.append(f"Generado: {now}")
    lines.append("")
    lines.append(
        "NOTA: Las contraseñas NO se pueden leer desde la base de datos (solo se guarda el hash)."
    )
    lines.append(
        "Este archivo incluye contraseñas cuando se pueden inferir por seeds (usuarios_demo.txt / usuarios_personas.txt)."
    )
    lines.append("")

    lines.append("SUPERADMIN")
    if superadmins:
        for u in sorted(superadmins, key=lambda x: x["username"]):
            lines.append(f"- {u['username']} / password: {u['password']}")
    else:
        lines.append("- (ninguno)")
    lines.append("")

    if no_group:
        lines.append("USUARIOS SIN GRUPO")
        for u in sorted(no_group, key=lambda x: (x["role"], x["username"])):
            lines.append(f"- {u['username']} ({u['role']}) / password: {u['password']}")
        lines.append("")

    # Grupos en orden
    ordered_group_ids = [gid for (gid, _) in group_order]
    # incluye grupos que aparezcan en users pero no existan en tabla (por si acaso)
    for gid in sorted(groups.keys()):
        if gid not in ordered_group_ids:
            ordered_group_ids.append(gid)

    for gid in ordered_group_ids:
        g = groups[gid]
        lines.append(f"GRUPO: {g['nombre']} (id={g['id']})")

        lines.append("  Admin(es) de grupo:")
        if g["admins"]:
            for u in sorted(g["admins"], key=lambda x: x["username"]):
                lines.append(f"    - {u['username']} / password: {u['password']}")
        else:
            lines.append("    - (ninguno)")

        lines.append("  Técnicos:")
        if g["techs"]:
            for u in sorted(g["techs"], key=lambda x: x["username"]):
                lines.append(f"    - {u['username']} / password: {u['password']}")
        else:
            lines.append("    - (ninguno)")

        lines.append(f"  Usuarios (reporters) [{len(g['reporters'])}]:")
        if g["reporters"]:
            for u in sorted(g["reporters"], key=lambda x: x["username"]):
                lines.append(f"    - {u['username']} / password: {u['password']}")
        else:
            lines.append("    - (ninguno)")

        if g["others"]:
            lines.append("  Otros:")
            for u in sorted(g["others"], key=lambda x: (x["role"], x["username"])):
                lines.append(f"    - {u['username']} ({u['role']}) / password: {u['password']}")

        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

    return output_path


async def _resolve_group_id(grupo_id: str | None, grupo_nombre: str | None):
    if grupo_id:
        return int(grupo_id)
    if grupo_nombre:
        grupo = await main.get_grupo_by_nombre(grupo_nombre)
        if not grupo:
            raise ValueError(f"Grupo '{grupo_nombre}' no existe")
        return int(grupo["id"])
    return None


async def _run(args):
    await main.init_db_pool(asyncio.get_running_loop())
    try:
        if args.command == "list-users":
            rows = await main.list_users()
            _print_users(rows)
            return

        if args.command == "create-group":
            grupo_id = await main.create_grupo(args.nombre, args.descripcion or "")
            print(f"Grupo creado: id={grupo_id} nombre={args.nombre}")
            return

        if args.command == "create-user":
            role = _resolve_role(args.role)
            password = args.password
            if not password:
                password = getpass.getpass("Password: ")

            gid = await _resolve_group_id(args.grupo_id, args.grupo)
            user_id = await main.create_user(args.username, password, role=role, grupo_id=gid)
            print(
                f"Usuario creado: id={user_id} username={args.username} role={role} grupo_id={gid}"
            )
            return

        if args.command == "assign-user":
            role = _resolve_role(args.role)
            gid = await _resolve_group_id(args.grupo_id, args.grupo)
            if not gid:
                raise ValueError("Debes indicar --grupo-id o --grupo")

            target = await main.get_user_by_username(args.username)
            if not target:
                raise ValueError(f"Usuario '{args.username}' no existe")

            await main.assign_user_to_group(int(target["id"]), int(gid), role)
            print(
                f"Asignado: username={args.username} -> grupo_id={gid} role={role}"
            )
            return

        if args.command == "set-role":
            role = _resolve_role(args.role)
            target = await main.get_user_by_username(args.username)
            if not target:
                raise ValueError(f"Usuario '{args.username}' no existe")
            await main.set_user_role(int(target["id"]), role)
            print(f"Rol actualizado: username={args.username} role={role}")
            return

        if args.command == "set-group":
            gid = await _resolve_group_id(args.grupo_id, args.grupo)
            target = await main.get_user_by_username(args.username)
            if not target:
                raise ValueError(f"Usuario '{args.username}' no existe")
            await main.set_user_group(int(target["id"]), gid)
            print(f"Grupo actualizado: username={args.username} grupo_id={gid}")
            return

        if args.command == "seed-demo":
            output_path = await _seed_demo(args.output)
            print(f"Seed demo completado. Archivo generado: {output_path}")
            return

        if args.command == "seed-people":
            output_path = await _seed_people(
                args.output,
                total_users=args.users,
                incidents_per_group=args.incidents_per_group,
                password=args.password,
            )
            print(f"Seed personas completado. Archivo generado: {output_path}")
            return

        if args.command == "export-accounts":
            output_path = await _export_accounts(
                args.output,
                demo_file=args.demo_file,
                people_file=args.people_file,
            )
            print(f"Export completado. Archivo generado: {output_path}")
            return

        raise ValueError("Comando no reconocido")
    finally:
        await main.close_db_pool()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manage.py",
        description=(
            "Gestión de usuarios/grupos para la app de incidencias.\n\n"
            "Ejemplos:\n"
            "  python manage.py create-group --nombre IT --descripcion 'Soporte'\n"
            "  python manage.py create-user --username juan --role reporter --password 1234\n"
            "  python manage.py create-user --username ana --role technician --password 1234 --grupo IT\n"
            "  python manage.py create-user --username admin_it --role group_admin --password 1234 --grupo IT\n"
            "  python manage.py list-users\n"
            "  python manage.py seed-demo --output usuarios_demo.txt\n"
        ),
    )

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("list-users", help="Lista usuarios")

    sp = sub.add_parser("create-group", help="Crea un grupo")
    sp.add_argument("--nombre", required=True)
    sp.add_argument("--descripcion", default="")

    sp = sub.add_parser("create-user", help="Crea un usuario")
    sp.add_argument("--username", required=True)
    sp.add_argument(
        "--role",
        default="reporter",
        help="reporter | technician | group_admin | superadmin",
    )
    sp.add_argument("--password", default="")
    sp.add_argument("--grupo-id", default="")
    sp.add_argument("--grupo", default="")

    sp = sub.add_parser(
        "assign-user",
        help="Asigna usuario a grupo y (opcional) cambia el role",
    )
    sp.add_argument("--username", required=True)
    sp.add_argument(
        "--role",
        default="technician",
        help="reporter | technician | group_admin | superadmin",
    )
    sp.add_argument("--grupo-id", default="")
    sp.add_argument("--grupo", default="")

    sp = sub.add_parser("set-role", help="Cambia el role de un usuario")
    sp.add_argument("--username", required=True)
    sp.add_argument(
        "--role",
        required=True,
        help="reporter | technician | group_admin | superadmin",
    )

    sp = sub.add_parser("set-group", help="Cambia el grupo de un usuario")
    sp.add_argument("--username", required=True)
    sp.add_argument("--grupo-id", default="")
    sp.add_argument("--grupo", default="")

    sp = sub.add_parser(
        "seed-demo",
        help="Crea los 5 grupos (Informatica, RRHH/Administración, Escuela, Limpieza, Mantenimiento) y usuarios demo, y genera un TXT",
    )
    sp.add_argument(
        "--output",
        default="usuarios_demo.txt",
        help="Nombre/path del TXT a generar (por defecto: usuarios_demo.txt)",
    )

    sp = sub.add_parser(
        "seed-people",
        help="Genera usuarios con nombres realistas y crea incidencias por grupo (datos de prueba)",
    )
    sp.add_argument(
        "--users",
        type=int,
        default=50,
        help="Número de usuarios reporter a crear (total). Por defecto: 50",
    )
    sp.add_argument(
        "--incidents-per-group",
        type=int,
        default=50,
        help="Número de incidencias a crear por grupo. Por defecto: 50",
    )
    sp.add_argument(
        "--password",
        default="1234",
        help="Password simple para usuarios creados/actualizados. Por defecto: 1234",
    )
    sp.add_argument(
        "--output",
        default="usuarios_personas.txt",
        help="Nombre/path del TXT a generar (por defecto: usuarios_personas.txt)",
    )

    sp = sub.add_parser(
        "export-accounts",
        help="Exporta un TXT con todas las cuentas actuales (usuarios) de la BD",
    )
    sp.add_argument(
        "--output",
        default="cuentas_todas.txt",
        help="Nombre/path del TXT a generar (por defecto: cuentas_todas.txt)",
    )
    sp.add_argument(
        "--demo-file",
        default="usuarios_demo.txt",
        help="TXT del seed-demo (para inferir passwords). Por defecto: usuarios_demo.txt",
    )
    sp.add_argument(
        "--people-file",
        default="usuarios_personas.txt",
        help="TXT del seed-people (para inferir passwords). Por defecto: usuarios_personas.txt",
    )

    return p


def main_cli():
    parser = build_parser()
    args = parser.parse_args()

    # Normaliza strings vacíos a None
    for k in ("grupo_id", "grupo"):
        if hasattr(args, k) and getattr(args, k) == "":
            setattr(args, k, None)

    try:
        asyncio.run(_run(args))
    except Exception as e:
        print(f"ERROR: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main_cli()
