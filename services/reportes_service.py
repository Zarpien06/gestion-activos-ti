import pandas as pd


def exportar_excel(datos, archivo):

    df = pd.DataFrame(datos)

    df.to_excel(
        archivo,
        index=False
    )

    return archivo