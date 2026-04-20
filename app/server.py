# server.py
from sanic import Sanic
from sanic.response import redirect, text, html
from sanic_ext import Extend, render
from functools import wraps
import uuid
import logging
import asyncio
import os

import config
import main

logger = logging.getLogger("incidencias.server")
logger.setLevel(logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Sanic("IncidenciasApp")
# Asegura que Jinja encuentre las plantillas aunque ejecutes el comando desde otra carpeta
app.config.TEMPLATING_PATH_TO_TEMPLATES = os.path.join(BASE_DIR, "templates")
Extend(app)

# Configuración
app.config.SECRET_KEY = config.SECRET_KEY
app.config.SESSION_COOKIE_NAME = config.SESSION_COOKIE_NAME
app.config.TEMPLATING_AUTO_RELOAD = True
app.config.TEMPLATING_ENABLE_ASYNC = True

# Almacenamiento de sesiones en memoria (simple)
app.ctx.sessions = {}

# Inicialización del pool de conexiones antes de arrancar el servidor
@app.before_server_start
async def setup_db(app, loop):
    # Sanic pasa loop implícitamente en versiones antiguas; en 23.x usamos asyncio.get_event_loop()
    loop = asyncio.get_event_loop()
    await main.init_db_pool(loop)
    app.ctx.db_pool = main.db_pool

# Middleware para cargar sesión desde cookies
@app.middleware("request")
async def load_session(request):
    session_id = request.cookies.get(app.config.SESSION_COOKIE_NAME)
    if session_id:
        request.ctx.session = app.ctx.sessions.get(session_id, {})
    else:
        request.ctx.session = {}

# Middleware para guardar sesión en cookies
@app.middleware("response")
async def save_session(request, response):
    if hasattr(request.ctx, "session") and request.ctx.session:
        session_id = request.cookies.get(app.config.SESSION_COOKIE_NAME)
        if not session_id:
            session_id = str(uuid.uuid4())
            response.cookies[app.config.SESSION_COOKIE_NAME] = session_id
        app.ctx.sessions[session_id] = request.ctx.session

# Helpers / Decoradores

def _get_user(request):
    return getattr(request.ctx, "session", {}).get("user")


def _user_role(user) -> str:
    return main.normalize_role((user or {}).get("role"))


def _is_superadmin(user) -> bool:
    return bool((user or {}).get("is_admin")) or _user_role(user) == main.ROLE_SUPERADMIN


def _forbidden():
    return text("Acceso denegado", status=403)


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _can_create_incidencia(user) -> bool:
    if _is_superadmin(user):
        return True
    return _user_role(user) in {main.ROLE_REPORTER, main.ROLE_GROUP_ADMIN}


def login_required(handler):
    @wraps(handler)
    async def wrapper(request, *args, **kwargs):
        if not _get_user(request):
            return redirect("/login")
        return await handler(request, *args, **kwargs)

    return wrapper


def superadmin_required(handler):
    @wraps(handler)
    async def wrapper(request, *args, **kwargs):
        user = _get_user(request)
        if not user:
            return redirect("/login")
        if not _is_superadmin(user):
            return _forbidden()
        return await handler(request, *args, **kwargs)

    return wrapper


def group_admin_required(handler):
    """Group admin o superadmin."""

    @wraps(handler)
    async def wrapper(request, *args, **kwargs):
        user = _get_user(request)
        if not user:
            return redirect("/login")
        if _is_superadmin(user):
            return await handler(request, *args, **kwargs)
        if _user_role(user) != main.ROLE_GROUP_ADMIN:
            return _forbidden()
        return await handler(request, *args, **kwargs)

    return wrapper

# Rutas
@app.get("/")
@login_required
async def index(request):
    user = _get_user(request)
    if _user_role(user) == main.ROLE_REPORTER:
        return redirect("/incidencias/crear")
    return redirect("/incidencias")

@app.get("/login")
async def login_get(request):
    return await render("login.html", context={"error": None}, app=app)

@app.post("/login")
async def login_post(request):
    form = request.form
    username = form.get("username")
    password = form.get("password")
    try:
        user = await main.verify_user(username, password)
    except Exception as e:
        logger.exception("Error verificando usuario: %s", e)
        return await render(
            "login.html",
            context={"error": "Error interno al verificar credenciales"},
            app=app,
        )
    if user:
        request.ctx.session["user"] = {
            "id": user["id"],
            "username": user["username"],
            "role": user.get("role", main.ROLE_REPORTER),
            "grupo_id": user.get("grupo_id"),
            "is_admin": user["is_admin"],
        }
        if main.normalize_role(user.get("role")) == main.ROLE_REPORTER:
            return redirect("/incidencias/crear")
        return redirect("/incidencias")
    return await render(
        "login.html",
        context={"error": "Credenciales incorrectas"},
        app=app,
    )

@app.get("/logout")
@login_required
async def logout(request):
    request.ctx.session.clear()
    return redirect("/login")

@app.get("/incidencias")
@login_required
async def incidencias_list(request):
    user = _get_user(request)
    role = _user_role(user)

    scope = (request.args.get("scope") or "all").strip().lower()
    if role == main.ROLE_REPORTER:
        scope = "created"
    elif scope not in {"all", "mine", "group", "created"}:
        scope = "all"

    incidencias = await main.get_incidencias_for_user(user, scope=scope)

    closed_states = {"cerrada", "resuelta", "resuelto"}
    incidencias_abiertas = [
        i
        for i in (incidencias or [])
        if (str(i.get("estado") or "").strip().lower() not in closed_states)
    ]
    incidencias_cerradas = [
        i
        for i in (incidencias or [])
        if (str(i.get("estado") or "").strip().lower() in closed_states)
    ]

    group_admin = None
    if user.get("grupo_id"):
        group_admin = await main.get_group_admin_for_group(int(user["grupo_id"]))

    return await render(
        "incidencias.html",
        context={
            "incidencias_abiertas": incidencias_abiertas,
            "incidencias_cerradas": incidencias_cerradas,
            "user": user,
            "scope": scope,
            "group_admin": group_admin,
        },
        app=app,
    )

@app.get("/incidencias/crear")
@login_required
async def crear_incidencia_get(request):
    user = _get_user(request)
    if not _can_create_incidencia(user):
        return _forbidden()

    grupos = await main.get_all_grupos()
    if not _is_superadmin(user) and _user_role(user) == main.ROLE_GROUP_ADMIN:
        if not user.get("grupo_id"):
            return _forbidden()
        grupos = [g for g in grupos if g.get("id") == user.get("grupo_id")]

    return await render(
        "crear_incidencia.html",
        context={"grupos": grupos, "user": user},
        app=app,
    )

@app.post("/incidencias/crear")
@login_required
async def crear_incidencia_post(request):
    user = _get_user(request)
    if not _can_create_incidencia(user):
        return _forbidden()

    form = request.form
    titulo = form.get("titulo")
    descripcion = form.get("descripcion")
    prioridad = form.get("prioridad")
    grupo_id = _to_int(form.get("grupo_id"))

    if not _is_superadmin(user) and _user_role(user) == main.ROLE_GROUP_ADMIN:
        grupo_id = _to_int(user.get("grupo_id"))

    user_id = user["id"]
    await main.create_incidencia(titulo, descripcion, prioridad, user_id, grupo_id)
    return redirect("/incidencias")

@app.get("/incidencias/<incidencia_id:int>")
@login_required
async def detalle_incidencia(request, incidencia_id):
    incidencia = await main.get_incidencia_by_id(incidencia_id)
    if not incidencia:
        return await render(
            "error.html",
            context={"mensaje": "Incidencia no encontrada", "user": _get_user(request)},
            app=app,
        )

    user = _get_user(request)

    if _user_role(user) == main.ROLE_REPORTER and incidencia.get("creador_id") != user.get("id"):
        return _forbidden()

    if (not _is_superadmin(user)) and _user_role(user) == main.ROLE_GROUP_ADMIN:
        if not user.get("grupo_id") or incidencia.get("grupo_id") != user.get("grupo_id"):
            return _forbidden()

    return await render(
        "detalle_incidencia.html",
        context={"incidencia": incidencia, "user": user},
        app=app,
    )

@app.get("/incidencias/<incidencia_id:int>/editar")
@group_admin_required
async def editar_incidencia_get(request, incidencia_id):
    user = _get_user(request)

    incidencia = await main.get_incidencia_by_id(incidencia_id)
    if not incidencia:
        return await render(
            "error.html",
            context={"mensaje": "Incidencia no encontrada", "user": user},
            app=app,
        )

    # Group admin solo puede editar incidencias de su grupo
    if not _is_superadmin(user):
        if not user.get("grupo_id") or incidencia.get("grupo_id") != user.get("grupo_id"):
            return _forbidden()

    grupos = await main.get_all_grupos()
    if not _is_superadmin(user) and user.get("grupo_id"):
        grupos = [g for g in grupos if g.get("id") == user.get("grupo_id")]

    tecnicos = []
    grupo_para_asignacion = incidencia.get("grupo_id") or user.get("grupo_id")
    if grupo_para_asignacion:
        tecnicos = await main.get_technicians_by_group(int(grupo_para_asignacion))

    return await render(
        "crear_incidencia.html",
        context={
            "incidencia": incidencia,
            "grupos": grupos,
            "tecnicos": tecnicos,
            "editar": True,
            "user": user,
        },
        app=app,
    )

@app.post("/incidencias/<incidencia_id:int>/editar")
@group_admin_required
async def editar_incidencia_post(request, incidencia_id):
    user = _get_user(request)

    incidencia = await main.get_incidencia_by_id(incidencia_id)
    if not incidencia:
        return await render(
            "error.html",
            context={"mensaje": "Incidencia no encontrada", "user": user},
            app=app,
        )

    if not _is_superadmin(user):
        if not user.get("grupo_id") or incidencia.get("grupo_id") != user.get("grupo_id"):
            return _forbidden()

    form = request.form
    titulo = form.get("titulo")
    descripcion = form.get("descripcion")
    estado = form.get("estado")
    prioridad = form.get("prioridad")

    asignado_id = _to_int(form.get("asignado_id"))
    grupo_id = _to_int(form.get("grupo_id"))

    if not _is_superadmin(user):
        # Un group admin no puede mover incidencias a otro grupo
        grupo_id = int(user.get("grupo_id"))

    await main.update_incidencia(
        incidencia_id,
        titulo,
        descripcion,
        estado,
        prioridad,
        asignado_id,
        grupo_id,
    )
    return redirect(f"/incidencias/{incidencia_id}")

# Administración de grupos
@app.get("/admin/grupos/crear")
@superadmin_required
async def crear_grupo_get(request):
    user = _get_user(request)
    return await render("crear_grupo.html", context={"user": user}, app=app)

@app.post("/admin/grupos/crear")
@superadmin_required
async def crear_grupo_post(request):
    user = _get_user(request)
    form = request.form
    nombre = form.get("nombre")
    descripcion = form.get("descripcion")
    if not nombre:
        return await render(
            "crear_grupo.html",
            context={"user": user, "error": "El nombre es obligatorio"},
            app=app,
        )
    await main.create_grupo(nombre, descripcion)
    return redirect("/incidencias")

@app.get("/admin/grupos/asignar")
@group_admin_required
async def asignar_grupo_get(request):
    user = _get_user(request)
    grupos = await main.get_all_grupos()

    if not _is_superadmin(user):
        if not user.get("grupo_id"):
            return _forbidden()
        grupos = [g for g in grupos if g.get("id") == user.get("grupo_id")]

    return await render(
        "asignar_grupo.html",
        context={"grupos": grupos, "user": user},
        app=app,
    )

@app.post("/admin/grupos/asignar")
@group_admin_required
async def asignar_grupo_post(request):
    user = _get_user(request)

    form = request.form
    username = (form.get("username") or "").strip()
    rol = (form.get("rol") or "technician").strip().lower()

    if not username:
        return _forbidden()

    target = await main.get_user_by_username(username)
    if not target:
        grupos = await main.get_all_grupos()
        if not _is_superadmin(user):
            grupos = [g for g in grupos if g.get("id") == user.get("grupo_id")]
        return await render(
            "asignar_grupo.html",
            context={
                "grupos": grupos,
                "user": user,
                "error": f"Usuario '{username}' no existe",
            },
            app=app,
        )

    if _is_superadmin(user):
        grupo_id = _to_int(form.get("grupo_id"))
    else:
        grupo_id = _to_int(user.get("grupo_id"))

    if not grupo_id:
        return _forbidden()

    await main.assign_user_to_group(int(target["id"]), int(grupo_id), rol)
    return redirect("/incidencias")

# Manejo de errores: siempre devuelve un Response válido
@app.exception(Exception)
async def handle_exceptions(request, exception):
    logger.exception(
        "Excepción no controlada en request %s: %s",
        getattr(request, "path", "<unknown>"),
        exception,
    )
    try:
        return await render(
            "error.html",
            context={"mensaje": str(exception), "user": _get_user(request)},
            app=app,
        )
    except Exception:
        # Fallback seguro
        return html(
            "<h1>Error interno</h1><p>Se ha producido un error en el servidor.</p>",
            status=500,
        )

# Archivos estáticos
app.static("/static", os.path.join(BASE_DIR, "static"))

if __name__ == "__main__":
    # Por defecto, en local escucha solo en localhost (127.0.0.1)
    # Puedes sobrescribirlo con variables de entorno:
    # - APP_HOST (ej: 0.0.0.0)
    # - APP_PORT (ej: 8000)
    # - APP_DEBUG (1/0, true/false)
    # - APP_AUTO_RELOAD (1/0, true/false)
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))

    debug = (os.getenv("APP_DEBUG", "1") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    auto_reload = (os.getenv("APP_AUTO_RELOAD", "1") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }

    app.run(host=host, port=port, debug=debug, auto_reload=auto_reload)
