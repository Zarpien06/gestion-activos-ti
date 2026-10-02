PASOS
1. Copia api/, templates/recolector/ y crear_token.py a la raiz del proyecto.
2. Ejecuta sql/modulo_recolector.sql en Supabase.
3. Aplica INTEGRACION_APP.txt.
4. Ejecuta python crear_token.py y guarda el token.
5. En el PC Windows configura:
   setx INVENTARIO_API_URL "https://TU-SERVICIO.onrender.com"
   setx INVENTARIO_RECOLECTOR_TOKEN "TOKEN_GENERADO"
6. En recolector_cliente ejecuta build_exe.bat.
7. Prueba primero en 3 equipos.
