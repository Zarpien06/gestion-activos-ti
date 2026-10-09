from supabase_client import supabase


class PersonaDuplicadaError(ValueError):
    """Se lanza cuando documento, correo o teléfono ya existen."""


def _limpiar(valor):
    valor = (valor or "").strip()
    return valor or None


def _limpiar_documento(valor):
    # Quita puntos, espacios y guiones: "1.234.567" == "1234567"
    valor = _limpiar(valor)
    if not valor:
        return None
    return "".join(c for c in valor if c.isalnum()) or None


def _limpiar_telefono(valor):
    valor = _limpiar(valor)
    if not valor:
        return None
    return "".join(c for c in valor if c.isdigit() or c == "+") or None


def _limpiar_correo(valor):
    # Siempre en minúsculas: así basta una comparación exacta (eq)
    valor = _limpiar(valor)
    return valor.lower() if valor else None


def _existe(campo, valor, excluir_id=None):
    if valor is None:
        return False

    consulta = (
        supabase.table("personas")
        .select("id")
        .eq(campo, valor)
        .limit(1)
    )

    # excluir_id evita que la persona choque consigo misma al editar
    if excluir_id is not None:
        consulta = consulta.neq("id", excluir_id)

    return bool(consulta.execute().data)


def _validar_unicos(documento, correo, telefono, excluir_id=None):
    errores = []

    if _existe("documento", documento, excluir_id):
        errores.append("Ya existe una persona con ese documento.")
    if _existe("correo", correo, excluir_id):
        errores.append("Ya existe una persona con ese correo.")
    if _existe("telefono", telefono, excluir_id):
        errores.append("Ya existe una persona con ese teléfono.")

    if errores:
        raise PersonaDuplicadaError(" ".join(errores))


def _es_error_unico(exc):
    # PostgREST devuelve el código 23505 en violaciones de unicidad
    return "23505" in str(exc) or "duplicate key" in str(exc).lower()


def obtener_personas():
    respuesta = supabase.table("personas").select("*").order("nombre").execute()
    return respuesta.data or []


def obtener_persona(persona_id):
    respuesta = (
        supabase.table("personas").select("*")
        .eq("id", persona_id).limit(1).execute()
    )
    return respuesta.data[0] if respuesta.data else None


def crear_persona(nombre, documento=None, correo=None, cargo=None, area=None, telefono=None):
    documento = _limpiar_documento(documento)
    correo = _limpiar_correo(correo)
    telefono = _limpiar_telefono(telefono)

    _validar_unicos(documento, correo, telefono)

    datos = {
        "nombre": nombre.strip(),
        "documento": documento,
        "correo": correo,
        "telefono": telefono,
        "cargo": _limpiar(cargo),
        "area": _limpiar(area),
        "estado": "Activo",
    }

    try:
        return supabase.table("personas").insert(datos).execute().data
    except Exception as exc:
        if _es_error_unico(exc):
            raise PersonaDuplicadaError(
                "Documento, correo o teléfono ya registrados."
            ) from exc
        raise


def actualizar_persona(persona_id, nombre, documento=None, correo=None,
                       cargo=None, area=None, telefono=None):
    documento = _limpiar_documento(documento)
    correo = _limpiar_correo(correo)
    telefono = _limpiar_telefono(telefono)

    _validar_unicos(documento, correo, telefono, excluir_id=persona_id)

    datos = {
        "nombre": nombre.strip(),
        "documento": documento,
        "correo": correo,
        "telefono": telefono,
        "cargo": _limpiar(cargo),
        "area": _limpiar(area),
    }

    try:
        return (
            supabase.table("personas").update(datos)
            .eq("id", persona_id).execute().data
        )
    except Exception as exc:
        if _es_error_unico(exc):
            raise PersonaDuplicadaError(
                "Documento, correo o teléfono ya registrados."
            ) from exc
        raise


def desactivar_persona(persona_id):
    return (
        supabase.table("personas").update({"estado": "Inactivo"})
        .eq("id", persona_id).execute().data
    )