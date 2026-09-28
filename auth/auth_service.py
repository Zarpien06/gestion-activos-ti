from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from auth.usuario import Usuario
from supabase_client import supabase


def obtener_usuario_por_username(username):
    username = str(username or "").strip().lower()
    if not username:
        return None

    respuesta = (
        supabase
        .table("usuarios")
        .select("*")
        .ilike("username", username)
        .limit(1)
        .execute()
    )
    return respuesta.data[0] if respuesta.data else None


def obtener_usuario_por_correo(correo):
    correo = str(correo or "").strip().lower()
    if not correo:
        return None

    respuesta = (
        supabase
        .table("usuarios")
        .select("*")
        .ilike("correo", correo)
        .limit(1)
        .execute()
    )
    return respuesta.data[0] if respuesta.data else None


def obtener_usuario_por_id(usuario_id):
    try:
        usuario_id = int(usuario_id)
    except (TypeError, ValueError):
        return None

    respuesta = (
        supabase
        .table("usuarios")
        .select("*")
        .eq("id", usuario_id)
        .limit(1)
        .execute()
    )
    return respuesta.data[0] if respuesta.data else None


def obtener_rol(rol_id):
    if not rol_id:
        return None

    respuesta = (
        supabase
        .table("roles")
        .select("*")
        .eq("id", rol_id)
        .limit(1)
        .execute()
    )
    return respuesta.data[0] if respuesta.data else None


def obtener_permisos_del_rol(rol_id):
    respuesta = (
        supabase
        .table("permisos")
        .select(
            "puede_ver,puede_crear,puede_editar,puede_eliminar,"
            "modulos(codigo,nombre)"
        )
        .eq("rol_id", rol_id)
        .execute()
    )

    permisos = {}
    for fila in respuesta.data or []:
        modulo = fila.get("modulos") or {}
        codigo = str(modulo.get("codigo") or "").strip().lower()
        if not codigo:
            continue

        permisos[codigo] = {
            "puede_ver": bool(fila.get("puede_ver")),
            "puede_crear": bool(fila.get("puede_crear")),
            "puede_editar": bool(fila.get("puede_editar")),
            "puede_eliminar": bool(fila.get("puede_eliminar")),
        }

    return permisos


def construir_usuario(registro):
    if not registro or not registro.get("activo", False):
        return None

    rol = obtener_rol(registro.get("rol_id"))
    if not rol or not rol.get("activo", True):
        return None

    usuario = Usuario(
        usuario_id=registro["id"],
        nombre=registro.get("nombre") or registro.get("username") or "Usuario",
        correo=registro.get("correo") or "",
        rol_id=rol["id"],
        rol_nombre=rol.get("nombre") or "Sin rol",
        activo=registro.get("activo", True),
        permisos=obtener_permisos_del_rol(rol["id"]),
    )
    usuario.username = registro.get("username") or ""
    return usuario


def autenticar_usuario(username, password):
    registro = obtener_usuario_por_username(username)
    if not registro or not registro.get("activo", False):
        return None

    password_hash = str(registro.get("password_hash") or "")
    if not password_hash or not check_password_hash(password_hash, str(password or "")):
        return None

    usuario = construir_usuario(registro)
    if usuario is None:
        return None

    try:
        (
            supabase
            .table("usuarios")
            .update({"ultimo_acceso": datetime.now(timezone.utc).isoformat()})
            .eq("id", registro["id"])
            .execute()
        )
    except Exception as error:
        print(f"No se pudo actualizar ultimo_acceso: {error}")

    return usuario


def cargar_usuario(usuario_id):
    try:
        return construir_usuario(obtener_usuario_por_id(usuario_id))
    except Exception as error:
        print(f"No se pudo cargar el usuario {usuario_id}: {error}")
        return None


def crear_usuario(nombre, username, correo, password, rol_id, activo=True):
    nombre = str(nombre or "").strip()
    username = str(username or "").strip().lower()
    correo = str(correo or "").strip().lower()
    password = str(password or "")

    if not nombre:
        raise ValueError("El nombre es obligatorio.")
    if not username:
        raise ValueError("El usuario es obligatorio.")
    if " " in username or len(username) < 4:
        raise ValueError("El usuario debe tener minimo 4 caracteres y no usar espacios.")
    if obtener_usuario_por_username(username):
        raise ValueError("Ese nombre de usuario ya esta registrado.")
    if correo and obtener_usuario_por_correo(correo):
        raise ValueError("Ese correo ya esta registrado.")
    if len(password) < 8:
        raise ValueError("La contrasena debe tener minimo 8 caracteres.")
    if not obtener_rol(rol_id):
        raise ValueError("El rol seleccionado no existe.")

    respuesta = (
        supabase
        .table("usuarios")
        .insert({
            "nombre": nombre,
            "username": username,
            "correo": correo or None,
            "password_hash": generate_password_hash(password),
            "rol_id": int(rol_id),
            "activo": bool(activo),
        })
        .execute()
    )
    return respuesta.data or []


def cambiar_password(usuario_id, password_nueva):
    password_nueva = str(password_nueva or "")
    if len(password_nueva) < 8:
        raise ValueError("La contrasena debe tener minimo 8 caracteres.")

    respuesta = (
        supabase
        .table("usuarios")
        .update({
            "password_hash": generate_password_hash(password_nueva),
            "actualizado_en": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", int(usuario_id))
        .execute()
    )
    return respuesta.data or []


def actualizar_usuario(usuario_id, nombre, username, correo, rol_id):
    nombre = str(nombre or "").strip()
    username = str(username or "").strip().lower()
    correo = str(correo or "").strip().lower()

    if not nombre:
        raise ValueError("El nombre es obligatorio.")
    if not username:
        raise ValueError("El usuario es obligatorio.")
    if " " in username or len(username) < 4:
        raise ValueError(
            "El usuario debe tener minimo 4 caracteres y no usar espacios."
        )

    usuario_existente = (
        supabase
        .table("usuarios")
        .select("id")
        .ilike("username", username)
        .neq("id", int(usuario_id))
        .limit(1)
        .execute()
    )
    if usuario_existente.data:
        raise ValueError("Ese nombre de usuario ya esta registrado.")

    if correo:
        correo_existente = (
            supabase
            .table("usuarios")
            .select("id")
            .ilike("correo", correo)
            .neq("id", int(usuario_id))
            .limit(1)
            .execute()
        )
        if correo_existente.data:
            raise ValueError("Ese correo ya esta registrado.")

    if obtener_rol(rol_id) is None:
        raise ValueError("El rol seleccionado no existe.")

    respuesta = (
        supabase
        .table("usuarios")
        .update({
            "nombre": nombre,
            "username": username,
            "correo": correo or None,
            "rol_id": int(rol_id),
            "actualizado_en": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", int(usuario_id))
        .execute()
    )
    return respuesta.data or []
