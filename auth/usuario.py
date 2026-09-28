from flask_login import UserMixin


class Usuario(UserMixin):
    def __init__(
        self,
        usuario_id,
        nombre,
        correo,
        rol_id,
        rol_nombre,
        activo=True,
        permisos=None,
    ):
        self.id = str(usuario_id)
        self.nombre = nombre
        self.correo = correo
        self.rol_id = rol_id
        self.rol_nombre = rol_nombre
        self.activo = bool(activo)
        self.permisos = permisos or {}

    @property
    def is_active(self):
        return self.activo

    @property
    def es_admin(self):
        return str(self.rol_nombre).strip().lower() == "administrador"

    def tiene_permiso(self, modulo, accion="ver"):
        if self.es_admin:
            return True

        modulo = str(modulo).strip().lower()
        accion = str(accion).strip().lower()

        acciones_validas = {
            "ver": "puede_ver",
            "crear": "puede_crear",
            "editar": "puede_editar",
            "eliminar": "puede_eliminar",
        }

        campo = acciones_validas.get(accion)
        if not campo:
            return False

        permisos_modulo = self.permisos.get(modulo, {})
        return bool(permisos_modulo.get(campo, False))

    def puede_ver(self, modulo):
        return self.tiene_permiso(modulo, "ver")

    def puede_crear(self, modulo):
        return self.tiene_permiso(modulo, "crear")

    def puede_editar(self, modulo):
        return self.tiene_permiso(modulo, "editar")

    def puede_eliminar(self, modulo):
        return self.tiene_permiso(modulo, "eliminar")
