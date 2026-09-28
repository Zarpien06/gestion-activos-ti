MODULO DE ACTAS PDF

1. Copia services/pdf_generator.py en tu proyecto.
2. Reemplaza templates/actas.html.
3. Abre app_routes_actas.py y copia sus imports y rutas hacia app.py.
4. Asegura que app.py ya tenga request, redirect, url_for y flash importados.
5. Instala ReportLab: pip install reportlab
6. Ejecuta: python -m py_compile services\pdf_generator.py
7. Ejecuta: python -m py_compile app.py
8. Inicia Flask y abre /actas.

Columnas esperadas en actas:
- persona_id
- tipo
- ruta_pdf
- observaciones
- fecha (default now)
