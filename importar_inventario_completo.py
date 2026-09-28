import math
import os
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client


# ==================================================
# CONFIGURACION
# ==================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ARCHIVO_EXCEL = Path(os.getenv("ARCHIVO_INVENTARIO", "uploads/inventario.xlsx"))
HOJA_INVENTARIO = os.getenv("HOJA_INVENTARIO", "Inventario")
TAMANO_LOTE = int(os.getenv("TAMANO_LOTE", "100"))
REINICIAR_DATOS = os.getenv("REINICIAR_DATOS", "NO").strip().upper() == "SI"

# Correcciones verificadas en el archivo entregado.
# La llave es el numero real de fila de Excel.
CORRECCIONES_CODIGO_POR_FILA = {
    306: "ACT-303",  # En Excel aparecia como |
    366: "ACT-304",  # Segunda aparicion de ACT-291
    598: "ACT-595",  # En Excel aparecia como ahhh
}

TABLAS_OPERATIVAS_EN_ORDEN_DE_BORRADO = [
    "actas",
    "tickets",
    "historial",
    "movimientos",
    "activos",
    "personas",
    "areas",
]

if not SUPABASE_URL:
    raise ValueError("No se encontro SUPABASE_URL en .env")
if not SUPABASE_KEY:
    raise ValueError("No se encontro SUPABASE_KEY en .env")
if not ARCHIVO_EXCEL.exists():
    raise FileNotFoundError(f"No existe el archivo: {ARCHIVO_EXCEL}")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ==================================================
# LIMPIEZA Y NORMALIZACION
# ==================================================

def normalizar_texto(valor):
    if valor is None:
        return ""
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto


def limpiar_valor(valor):
    if valor is None:
        return None
    if isinstance(valor, float) and math.isnan(valor):
        return None
    if isinstance(valor, pd.Timestamp):
        if pd.isna(valor):
            return None
        return valor.date().isoformat()
    if isinstance(valor, (datetime, date)):
        return valor.date().isoformat() if isinstance(valor, datetime) else valor.isoformat()

    texto = str(valor).strip()
    if normalizar_texto(texto) in {"", "nan", "none", "null", "nat"}:
        return None
    return texto

def limpiar_fecha(
    valor,
    fila_excel=None,
    columna=None
):
    """
    Convierte fechas válidas a formato YYYY-MM-DD.

    Las fechas incompletas, como '12 agosto',
    se guardan como None porque no incluyen año.
    """

    if valor is None:
        return None

    if isinstance(valor, float) and math.isnan(valor):
        return None

    if isinstance(valor, pd.Timestamp):
        if pd.isna(valor):
            return None

        return valor.date().isoformat()

    if isinstance(valor, datetime):
        return valor.date().isoformat()

    if isinstance(valor, date):
        return valor.isoformat()

    texto = str(valor).strip()

    if normalizar_texto(texto) in {
        "",
        "nan",
        "none",
        "null",
        "nat",
        "n/a",
        "na"
    }:
        return None

    formatos_permitidos = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%d.%m.%Y"
    ]

    for formato in formatos_permitidos:
        try:
            fecha = datetime.strptime(
                texto,
                formato
            )

            return fecha.date().isoformat()

        except ValueError:
            continue

    try:
        fecha_convertida = pd.to_datetime(
            texto,
            errors="coerce",
            dayfirst=True
        )

        if not pd.isna(fecha_convertida):
            return fecha_convertida.date().isoformat()

    except Exception:
        pass

    ubicacion = ""

    if fila_excel is not None:
        ubicacion += f" fila {fila_excel}"

    if columna:
        ubicacion += f", columna {columna}"

    print(
        "ADVERTENCIA: fecha incompleta o inválida"
        f"{ubicacion}: {texto!r}. "
        "Se guardará vacía."
    )

    return None

def limpiar_numero(valor):
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    try:
        numero = float(valor)
        return int(numero) if numero.is_integer() else numero
    except (TypeError, ValueError):
        return None


def canonicalizar_estado_fisico(valor):
    texto = normalizar_texto(valor)
    equivalencias = {
        "bueno": "Bueno",
        "regular": "Regular",
        "malo": "Malo",
        "danado": "Dañado",
    }
    return equivalencias.get(texto, limpiar_valor(valor) or "Bueno")


def canonicalizar_disponibilidad(valor):
    texto = normalizar_texto(valor)
    if texto == "asignado":
        return "Asignado"
    if texto == "disponible":
        return "Disponible"
    if "repar" in texto:
        return "En reparación"
    if "baja" in texto:
        return "Dado de baja"
    return limpiar_valor(valor) or "Disponible"


def buscar_columna(columnas, alternativas):
    mapa = {normalizar_texto(c): c for c in columnas}
    for alternativa in alternativas:
        encontrada = mapa.get(normalizar_texto(alternativa))
        if encontrada is not None:
            return encontrada
    return None


def detectar_fila_encabezado():
    vista = pd.read_excel(
        ARCHIVO_EXCEL,
        sheet_name=HOJA_INVENTARIO,
        header=None,
        nrows=30,
        engine="openpyxl",
    )
    for indice, fila in vista.iterrows():
        valores = {normalizar_texto(v) for v in fila.tolist()}
        if valores.intersection({"id activo", "id de activo", "codigo activo"}):
            return indice
    raise ValueError("No se encontro la fila del encabezado ID Activo.")


def es_persona_valida(nombre, disponibilidad):
    if not nombre:
        return False
    if disponibilidad not in {"Asignado", "En reparación"}:
        return False
    texto = normalizar_texto(nombre)
    palabras_no_persona = {
        "bodega",
        "disponible",
        "sin asignar",
        "no asignado",
        "n/a",
        "na",
    }
    return texto not in palabras_no_persona


# ==================================================
# LECTURA DEL ARCHIVO
# ==================================================

def preparar_datos():
    fila_encabezado = detectar_fila_encabezado()
    print(f"Encabezado encontrado en la fila {fila_encabezado + 1} de Excel.")

    df = pd.read_excel(
        ARCHIVO_EXCEL,
        sheet_name=HOJA_INVENTARIO,
        header=fila_encabezado,
        engine="openpyxl",
    )
    df.columns = [str(c).strip() for c in df.columns]

    columnas = {
        "codigo": buscar_columna(df.columns, ["ID Activo", "Codigo", "Código"]),
        "tipo": buscar_columna(df.columns, ["Tipo de Equipo", "Tipo"]),
        "marca": buscar_columna(df.columns, ["Marca"]),
        "modelo": buscar_columna(df.columns, ["Modelo"]),
        "serial": buscar_columna(df.columns, ["N° Serie", "Numero de Serie", "Serial"]),
        "procesador": buscar_columna(df.columns, ["Procesador"]),
        "ram_gb": buscar_columna(df.columns, ["RAM (GB)", "RAM"]),
        "disco_gb": buscar_columna(df.columns, ["Disco (GB)", "Disco"]),
        "sistema_operativo": buscar_columna(df.columns, ["S.O.", "Sistema Operativo"]),
        "estado": buscar_columna(df.columns, ["Estado"]),
        "disponibilidad": buscar_columna(df.columns, ["Disponibilidad"]),
        "asignado_a": buscar_columna(df.columns, ["Asignado a", "Asignado"]),
        "area": buscar_columna(df.columns, ["Area / Depto.", "Área / Depto.", "Area", "Área"]),
        "fecha_asignacion": buscar_columna(df.columns, ["Fecha Asignación", "Fecha Asignacion"]),
        "fecha_compra": buscar_columna(df.columns, ["Fecha Compra", "Fecha de Compra"]),
        "observaciones": buscar_columna(df.columns, ["Observaciones", "Observacion"]),
    }

    faltantes = [k for k in ["codigo", "tipo", "estado", "disponibilidad"] if not columnas[k]]
    if faltantes:
        raise ValueError(f"Faltan columnas obligatorias: {', '.join(faltantes)}")

    activos = []
    personas_candidatas = {}
    areas = {}
    codigos_vistos = set()
    correcciones = []
    errores = []

    for indice_df, fila in df.iterrows():
        fila_excel = fila_encabezado + 2 + indice_df
        codigo_original = limpiar_valor(fila.get(columnas["codigo"]))
        codigo = (codigo_original or "").strip().upper()

        if fila_excel in CORRECCIONES_CODIGO_POR_FILA:
            codigo_nuevo = CORRECCIONES_CODIGO_POR_FILA[fila_excel]
            correcciones.append({
                "fila_excel": fila_excel,
                "codigo_original": codigo,
                "codigo_nuevo": codigo_nuevo,
            })
            codigo = codigo_nuevo

        if not re.fullmatch(r"ACT-\d{3,}", codigo):
            if not any(limpiar_valor(v) for v in fila.tolist()):
                continue
            errores.append({
                "fila_excel": fila_excel,
                "motivo": "Codigo invalido",
                "valor": codigo_original,
            })
            continue

        if codigo in codigos_vistos:
            errores.append({
                "fila_excel": fila_excel,
                "motivo": "Codigo duplicado no corregido",
                "valor": codigo,
            })
            continue
        codigos_vistos.add(codigo)

        disponibilidad = canonicalizar_disponibilidad(
            fila.get(columnas["disponibilidad"])
        )
        nombre = limpiar_valor(fila.get(columnas["asignado_a"])) if columnas["asignado_a"] else None
        area = limpiar_valor(fila.get(columnas["area"])) if columnas["area"] else None

        # Un activo disponible o de baja no conserva responsable actual.
        if disponibilidad in {"Disponible", "Dado de baja"}:
            nombre = None
            area = None if disponibilidad == "Disponible" else area

        nombre_normalizado = normalizar_texto(nombre)
        area_normalizada = normalizar_texto(area)

        if area:
            areas.setdefault(area_normalizada, area.strip())

        if es_persona_valida(nombre, disponibilidad):
            persona = personas_candidatas.setdefault(
                nombre_normalizado,
                {
                    "nombre": nombre.strip(),
                    "areas": Counter(),
                },
            )
            if area:
                persona["areas"][area.strip()] += 1

        activos.append({
            "codigo": codigo,
            "tipo": limpiar_valor(fila.get(columnas["tipo"])) if columnas["tipo"] else None,
            "marca": limpiar_valor(fila.get(columnas["marca"])) if columnas["marca"] else None,
            "modelo": limpiar_valor(fila.get(columnas["modelo"])) if columnas["modelo"] else None,
            "serial": limpiar_valor(fila.get(columnas["serial"])) if columnas["serial"] else None,
            "procesador": limpiar_valor(fila.get(columnas["procesador"])) if columnas["procesador"] else None,
            "ram_gb": limpiar_numero(fila.get(columnas["ram_gb"])) if columnas["ram_gb"] else None,
            "disco_gb": limpiar_numero(fila.get(columnas["disco_gb"])) if columnas["disco_gb"] else None,
            "sistema_operativo": limpiar_valor(fila.get(columnas["sistema_operativo"])) if columnas["sistema_operativo"] else None,
            "estado": canonicalizar_estado_fisico(fila.get(columnas["estado"])),
            "disponibilidad": disponibilidad,
            "asignado_a": nombre,
            "area": area,
            "fecha_asignacion": limpiar_fecha(
    fila.get(
        columnas["fecha_asignacion"]
    ),
    fila_excel=fila_excel,
    columna="Fecha Asignacion"
)
if columnas["fecha_asignacion"]
else None,
            "fecha_compra": limpiar_fecha(
    fila.get(
        columnas["fecha_compra"]
    ),
    fila_excel=fila_excel,
    columna="Fecha Compra"
)
if columnas["fecha_compra"]
else None,
            "observaciones": limpiar_valor(fila.get(columnas["observaciones"])) if columnas["observaciones"] else None,
            "_nombre_normalizado": nombre_normalizado or None,
        })

    personas = []
    for clave, valor in personas_candidatas.items():
        area_principal = valor["areas"].most_common(1)[0][0] if valor["areas"] else None
        personas.append({
            "nombre": valor["nombre"],
            "nombre_normalizado": clave,
            "area": area_principal,
            "estado": "Activo",
        })

    lista_areas = [
        {"nombre": nombre, "nombre_normalizado": clave, "activo": True}
        for clave, nombre in sorted(areas.items(), key=lambda x: x[1])
    ]
    personas.sort(key=lambda x: x["nombre"].lower())
    activos.sort(key=lambda x: int(x["codigo"].split("-")[1]))

    print("\nResumen preparado")
    print(f"Areas: {len(lista_areas)}")
    print(f"Personas: {len(personas)}")
    print(f"Activos: {len(activos)}")
    print(f"Correcciones de codigo: {len(correcciones)}")
    print(f"Errores omitidos: {len(errores)}")

    return lista_areas, personas, activos, correcciones, errores


# ==================================================
# OPERACIONES EN SUPABASE
# ==================================================

def verificar_estructura_supabase():
    tablas = {
        "areas": (
            "id,nombre,nombre_normalizado,activo"
        ),
        "personas": (
            "id,nombre,nombre_normalizado,"
            "area,estado"
        ),
        "activos": (
            "id,codigo,tipo,marca,modelo,serial,"
            "procesador,ram_gb,disco_gb,"
            "sistema_operativo,estado,"
            "disponibilidad,persona_id,"
            "asignado_a,area,fecha_asignacion,"
            "fecha_compra,observaciones"
        ),
        "movimientos": (
            "id,activo_id,persona_id,"
            "accion,observacion,fecha"
        ),
        "historial": (
            "id,activo_id,accion,detalle,fecha"
        ),
        "tickets": (
            "id,activo_id"
        ),
        "actas": (
            "id,persona_id"
        ),
    }

    print(
        "\nVerificando estructura de Supabase..."
    )

    errores = []

    for tabla, columnas in tablas.items():
        try:
            (
                supabase
                .table(tabla)
                .select(columnas)
                .limit(1)
                .execute()
            )

            print(
                f"- {tabla}: estructura correcta"
            )

        except Exception as error:
            print(
                f"- {tabla}: estructura incompleta"
            )

            errores.append(
                f"{tabla}: {error}"
            )

    if errores:
        detalles = "\n".join(
            f"- {error}"
            for error in errores
        )

        raise RuntimeError(
            "La estructura de Supabase está "
            "incompleta.\n"
            "No se borró ningún dato.\n\n"
            f"{detalles}"
        )

    print(
        "Estructura completa. "
        "La importación puede continuar."
    )

def borrar_datos_operativos():
    if not REINICIAR_DATOS:
        raise RuntimeError(
            "Para borrar los datos operativos agrega REINICIAR_DATOS=SI en .env. "
            "Usuarios, roles, modulos y permisos NO se borran."
        )

    print("\nBorrando datos operativos...")
    for tabla in TABLAS_OPERATIVAS_EN_ORDEN_DE_BORRADO:
        try:
            supabase.table(tabla).delete().neq("id", -1).execute()
            print(f"- {tabla}: limpia")
        except Exception as error:
            raise RuntimeError(f"No se pudo limpiar {tabla}: {error}") from error


def insertar_lotes(
    tabla,
    registros
):
    total = len(registros)

    if total == 0:
        print(
            f"{tabla}: no hay registros para insertar"
        )
        return

    for inicio in range(
        0,
        total,
        TAMANO_LOTE
    ):
        lote = registros[
            inicio:inicio + TAMANO_LOTE
        ]

        try:
            (
                supabase
                .table(tabla)
                .insert(lote)
                .execute()
            )

        except Exception as error:
            numero_lote = (
                inicio // TAMANO_LOTE
            ) + 1

            raise RuntimeError(
                f"Error insertando {tabla}, "
                f"lote {numero_lote}: {error}"
            ) from error

        fin = min(
            inicio + TAMANO_LOTE,
            total
        )

        print(
            f"{tabla}: {fin} de {total}"
        )


def importar_todo():
    areas, personas, activos, correcciones, errores = preparar_datos()

    print("\nCorrecciones aplicadas:")
    for item in correcciones:
        print(
            f"- Fila {item['fila_excel']}: "
            f"{item['codigo_original']} -> {item['codigo_nuevo']}"
        )

        if errores:
            print("\nFilas omitidas:")

        for item in errores:
            print(
                f"- Fila {item['fila_excel']}: "
                f"{item['motivo']} "
                f"({item['valor']})"
            )

    verificar_estructura_supabase()

    borrar_datos_operativos()

    insertar_lotes(
        "areas",
        areas
    )

    insertar_lotes(
        "personas",
        personas
    )

    personas_db = (
        supabase
        .table("personas")
        .select("id,nombre,nombre_normalizado,area")
        .execute()
    ).data or []
    mapa_personas = {
        p["nombre_normalizado"]: p
        for p in personas_db
        if p.get("nombre_normalizado")
    }

    activos_finales = []
    for activo in activos:
        clave = activo.pop("_nombre_normalizado", None)
        persona = mapa_personas.get(clave) if clave else None
        activo["persona_id"] = persona["id"] if persona else None
        activos_finales.append(activo)

    insertar_lotes(
    "activos",
    activos_finales
)


    activos_db = (
        supabase
        .table("activos")
        .select("id,codigo,persona_id,disponibilidad,asignado_a")
        .execute()
    ).data or []

    movimientos = []
    historiales = []
    for activo in activos_db:
        detalle = (
            f"Activo {activo['codigo']} importado desde Inventario.xlsx. "
            f"Disponibilidad inicial: {activo.get('disponibilidad') or 'Sin estado'}."
        )
        movimientos.append({
            "activo_id": activo["id"],
            "persona_id": activo.get("persona_id"),
            "accion": "Importación inicial",
            "observacion": detalle,
        })
        historiales.append({
            "activo_id": activo["id"],
            "accion": "Importación inicial",
            "detalle": detalle,
        })

    insertar_lotes("movimientos", movimientos)
    insertar_lotes("historial", historiales)

    print("\nIMPORTACION COMPLETADA")
    print(f"Areas importadas: {len(areas)}")
    print(f"Personas importadas: {len(personas)}")
    print(f"Activos importados: {len(activos_finales)}")
    print(f"Movimientos iniciales: {len(movimientos)}")
    print("Usuarios, roles, modulos y permisos fueron conservados.")


if __name__ == "__main__":
    importar_todo()
