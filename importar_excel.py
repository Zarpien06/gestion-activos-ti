import os
import math
import unicodedata
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client


# ==================================================
# CONFIGURACIÓN
# ==================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

ARCHIVO_EXCEL = "uploads/inventario.xlsx"
HOJA_INVENTARIO = "Inventario"
TAMANO_LOTE = 100


if not SUPABASE_URL:
    raise ValueError(
        "No se encontró SUPABASE_URL en el archivo .env"
    )

if not SUPABASE_KEY:
    raise ValueError(
        "No se encontró SUPABASE_KEY en el archivo .env"
    )


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# ==================================================
# FUNCIONES DE LIMPIEZA
# ==================================================

def normalizar_texto(texto):
    """
    Convierte un texto a minúsculas y elimina tildes.
    Se utiliza para comparar encabezados.
    """

    if texto is None:
        return ""

    texto = str(texto).strip().lower()

    texto = unicodedata.normalize(
        "NFKD",
        texto
    )

    texto = "".join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    )

    return texto


def limpiar_valor(valor):
    """
    Convierte valores vacíos de Excel en None.
    """

    if valor is None:
        return None

    if isinstance(valor, float) and math.isnan(valor):
        return None

    texto = str(valor).strip()

    if texto.lower() in {
        "",
        "nan",
        "none",
        "null",
        "nat"
    }:
        return None

    return texto


def buscar_columna(columnas, alternativas):
    """
    Busca una columna aceptando diferentes nombres.
    """

    columnas_normalizadas = {
        normalizar_texto(columna): columna
        for columna in columnas
    }

    for alternativa in alternativas:
        alternativa_normalizada = normalizar_texto(
            alternativa
        )

        if alternativa_normalizada in columnas_normalizadas:
            return columnas_normalizadas[
                alternativa_normalizada
            ]

    return None


# ==================================================
# DETECTAR ENCABEZADO REAL
# ==================================================

def detectar_fila_encabezado():
    """
    Lee las primeras filas sin encabezado y busca
    la celda que contiene ID Activo.
    """

    vista_previa = pd.read_excel(
        ARCHIVO_EXCEL,
        sheet_name=HOJA_INVENTARIO,
        header=None,
        nrows=20,
        engine="openpyxl"
    )

    for indice_fila, fila in vista_previa.iterrows():
        for valor in fila.tolist():
            valor_normalizado = normalizar_texto(valor)

            if valor_normalizado in {
                "id activo",
                "id de activo",
                "codigo activo",
                "codigo de activo"
            }:
                print(
                    "Encabezado encontrado en la fila "
                    f"{indice_fila + 1} de Excel."
                )

                return indice_fila

    raise ValueError(
        "No se encontró una fila que contenga "
        "'ID Activo' en las primeras 20 filas."
    )


# ==================================================
# LEER Y PREPARAR EL EXCEL
# ==================================================

def preparar_registros():
    fila_encabezado = detectar_fila_encabezado()

    dataframe = pd.read_excel(
        ARCHIVO_EXCEL,
        sheet_name=HOJA_INVENTARIO,
        header=fila_encabezado,
        engine="openpyxl"
    )

    dataframe.columns = [
        str(columna).strip()
        for columna in dataframe.columns
    ]

    print("\nColumnas correctas encontradas:")

    for columna in dataframe.columns:
        print(f"- {columna}")

    columna_codigo = buscar_columna(
        dataframe.columns,
        [
            "ID Activo",
            "ID de Activo",
            "Código Activo",
            "Codigo Activo",
            "Código",
            "Codigo"
        ]
    )

    columna_tipo = buscar_columna(
        dataframe.columns,
        [
            "Tipo de Equipo",
            "Tipo de equipo",
            "Tipo"
        ]
    )

    columna_marca = buscar_columna(
        dataframe.columns,
        [
            "Marca"
        ]
    )

    columna_modelo = buscar_columna(
        dataframe.columns,
        [
            "Modelo"
        ]
    )

    columna_serial = buscar_columna(
        dataframe.columns,
        [
            "N° Serie",
            "Nº Serie",
            "No. Serie",
            "Número de Serie",
            "Numero de Serie",
            "Serial"
        ]
    )

    columna_estado = buscar_columna(
        dataframe.columns,
        [
            "Estado"
        ]
    )

    columna_disponibilidad = buscar_columna(
        dataframe.columns,
        [
            "Disponibilidad"
        ]
    )

    columna_asignado = buscar_columna(
        dataframe.columns,
        [
            "Asignado a",
            "Asignado"
        ]
    )

    columna_area = buscar_columna(
        dataframe.columns,
        [
            "Área / Depto.",
            "Area / Depto.",
            "Área",
            "Area",
            "Departamento"
        ]
    )

    columna_observaciones = buscar_columna(
        dataframe.columns,
        [
            "Observaciones",
            "Observación",
            "Observacion"
        ]
    )

    if columna_codigo is None:
        raise ValueError(
            "Se encontró el encabezado, pero no se pudo "
            "identificar la columna ID Activo."
        )

    print("\nColumnas utilizadas:")

    print(f"Código: {columna_codigo}")
    print(f"Tipo: {columna_tipo}")
    print(f"Marca: {columna_marca}")
    print(f"Modelo: {columna_modelo}")
    print(f"Serial: {columna_serial}")
    print(f"Estado físico: {columna_estado}")
    print(
        f"Disponibilidad: {columna_disponibilidad}"
    )
    print(f"Asignado a: {columna_asignado}")
    print(f"Área: {columna_area}")
    print(
        f"Observaciones: {columna_observaciones}"
    )

    registros_por_codigo = {}

    filas_omitidas = 0
    codigos_duplicados = 0

    for _, fila in dataframe.iterrows():
        codigo = limpiar_valor(
            fila.get(columna_codigo)
        )

        if codigo is None:
            filas_omitidas += 1
            continue

        codigo = codigo.strip()

        if not codigo.upper().startswith("ACT-"):
            print(
                f"Fila omitida por código inválido: {codigo}"
            )

            filas_omitidas += 1
            continue

        registro = {
            "codigo": codigo,

            "tipo": (
                limpiar_valor(fila.get(columna_tipo))
                if columna_tipo
                else None
            ),

            "marca": (
                limpiar_valor(fila.get(columna_marca))
                if columna_marca
                else None
            ),

            "modelo": (
                limpiar_valor(fila.get(columna_modelo))
                if columna_modelo
                else None
            ),

            "serial": (
                limpiar_valor(fila.get(columna_serial))
                if columna_serial
                else None
            ),

            "estado": (
                limpiar_valor(fila.get(columna_estado))
                if columna_estado
                else None
            ),

            "disponibilidad": (
                limpiar_valor(
                    fila.get(columna_disponibilidad)
                )
                if columna_disponibilidad
                else None
            ),

            "asignado_a": (
                limpiar_valor(fila.get(columna_asignado))
                if columna_asignado
                else None
            ),

            "area": (
                limpiar_valor(fila.get(columna_area))
                if columna_area
                else None
            ),

            "observaciones": (
                limpiar_valor(
                    fila.get(columna_observaciones)
                )
                if columna_observaciones
                else None
            )
        }

        if codigo in registros_por_codigo:
            codigos_duplicados += 1

            print(
                "Código duplicado en Excel, se conservará "
                f"la última fila: {codigo}"
            )

        registros_por_codigo[codigo] = registro

    registros = list(
        registros_por_codigo.values()
    )

    print("\nResumen de preparación:")

    print(
        f"Registros válidos: {len(registros)}"
    )

    print(
        f"Filas omitidas: {filas_omitidas}"
    )

    print(
        f"Códigos duplicados encontrados: "
        f"{codigos_duplicados}"
    )

    return registros


# ==================================================
# IMPORTAR EN SUPABASE
# ==================================================

def importar_registros():
    registros = preparar_registros()

    if not registros:
        print(
            "No se encontraron registros válidos "
            "para importar."
        )

        return

    print(
        "\nIniciando actualización en Supabase..."
    )

    total_registros = len(registros)

    errores = []

    for inicio in range(
        0,
        total_registros,
        TAMANO_LOTE
    ):
        lote = registros[
            inicio:inicio + TAMANO_LOTE
        ]

        try:
            (
                supabase
                .table("activos")
                .upsert(
                    lote,
                    on_conflict="codigo"
                )
                .execute()
            )

            fin = min(
                inicio + TAMANO_LOTE,
                total_registros
            )

            print(
                f"Procesados {fin} de "
                f"{total_registros}"
            )

        except Exception as error:
            numero_lote = (
                inicio // TAMANO_LOTE
            ) + 1

            print(
                f"Error en el lote {numero_lote}: "
                f"{error}"
            )

            errores.append({
                "lote": numero_lote,
                "error": str(error)
            })

    print("\nProceso terminado.")

    if errores:
        print(
            f"Se presentaron errores en "
            f"{len(errores)} lotes."
        )

        for error in errores:
            print(
                f"Lote {error['lote']}: "
                f"{error['error']}"
            )
    else:
        print(
            "Todos los registros fueron actualizados "
            "correctamente."
        )


# ==================================================
# EJECUCIÓN
# ==================================================

if __name__ == "__main__":
    importar_registros()