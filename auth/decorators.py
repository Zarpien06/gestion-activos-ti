from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user


def permiso_requerido(modulo, accion="ver"):
    def decorador(funcion):
        @wraps(funcion)
        def envoltura(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Debes iniciar sesion para continuar.", "warning")
                return redirect(
                    url_for("login", next=request.full_path)
                )

            if not current_user.tiene_permiso(modulo, accion):
                abort(403)

            return funcion(*args, **kwargs)

        return envoltura

    return decorador


def solo_administrador(funcion):
    @wraps(funcion)
    def envoltura(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Debes iniciar sesion para continuar.", "warning")
            return redirect(
                url_for("login", next=request.full_path)
            )

        if not current_user.es_admin:
            abort(403)

        return funcion(*args, **kwargs)

    return envoltura
