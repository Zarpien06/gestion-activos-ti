from flask_login import LoginManager


login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Debes iniciar sesion para acceder."
login_manager.login_message_category = "warning"


def inicializar_login(app):
    login_manager.init_app(app)

    from auth.auth_service import cargar_usuario

    @login_manager.user_loader
    def cargar_usuario_flask(usuario_id):
        return cargar_usuario(usuario_id)
