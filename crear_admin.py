from getpass import getpass

from auth.auth_service import crear_usuario
from supabase_client import supabase


def obtener_rol_administrador():
    respuesta = (
        supabase
        .table("roles")
        .select("*")
        .eq("nombre", "Administrador")
        .limit(1)
        .execute()
    )

    if not respuesta.data:
        return None

    return respuesta.data[0]


def main():
    print("=" * 40)
    print("CREAR USUARIO ADMINISTRADOR")
    print("=" * 40)

    rol = obtener_rol_administrador()

    if rol is None:
        print()
        print("ERROR: No existe el rol Administrador.")
        print("Primero crea el rol Administrador en Supabase.")
        return

    nombre = input("Nombre del administrador: ").strip()
    correo = input("Correo del administrador: ").strip().lower()
    password = getpass("Contrasena, minimo 8 caracteres: ")
    confirmar_password = getpass("Confirma la contrasena: ")

    if not nombre:
        print("ERROR: El nombre es obligatorio.")
        return

    if not correo or "@" not in correo:
        print("ERROR: El correo no es valido.")
        return

    if len(password) < 8:
        print("ERROR: La contrasena debe tener al menos 8 caracteres.")
        return

    if password != confirmar_password:
        print("ERROR: Las contrasenas no coinciden.")
        return

    try:
        crear_usuario(
            nombre=nombre,
            correo=correo,
            password=password,
            rol_id=rol["id"],
            activo=True,
        )

        print()
        print("=" * 40)
        print("ADMINISTRADOR CREADO CORRECTAMENTE")
        print("=" * 40)
        print(f"Nombre: {nombre}")
        print(f"Correo: {correo}")
        print("Ya puedes iniciar sesion en /login.")

    except Exception as error:
        print()
        print("=" * 40)
        print("NO FUE POSIBLE CREAR EL ADMINISTRADOR")
        print("=" * 40)
        print(f"Error: {error}")


if __name__ == "__main__":
    main()
