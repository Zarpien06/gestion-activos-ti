import json
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from cliente_api import enviar
from configuracion import API_URL
from obtener_hardware import recolectar

CAMPOS_RESUMEN = [
    ("Equipo", "hostname"),
    ("Usuario Windows", "usuario_windows"),
    ("Dominio", "dominio"),
    ("Marca", "marca"),
    ("Modelo", "modelo"),
    ("Serial BIOS", "bios_serial"),
    ("UUID", "uuid_equipo"),
    ("Procesador", "procesador"),
    ("RAM", "ram_gb"),
    ("Disco total", "disco_gb"),
    ("Sistema operativo", "sistema_operativo"),
    ("Versión", "version_sistema"),
    ("Arquitectura", "arquitectura"),
    ("Dirección IP", "direccion_ip"),
    ("Dirección MAC", "direccion_mac"),
]


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Recolector de Inventario TI")
        self.root.geometry("760x620")
        self.root.minsize(640, 500)
        self.datos = None
        self.ocupado = False

        ttk.Label(
            root,
            text="Recolector de Inventario TI",
            font=("Segoe UI", 18, "bold"),
        ).pack(pady=(14, 2))
        ttk.Label(
            root,
            text=f"Servidor: {API_URL}",
            foreground="#6b7280",
        ).pack()

        self.pestanas = ttk.Notebook(root)
        self.pestanas.pack(fill="both", expand=True, padx=16, pady=10)

        # Pestaña Resumen
        marco = ttk.Frame(self.pestanas)
        self.pestanas.add(marco, text="Resumen")
        self.tabla = ttk.Treeview(
            marco,
            columns=("valor",),
            show="tree headings",
            height=15,
        )
        self.tabla.heading("#0", text="Dato")
        self.tabla.heading("valor", text="Valor")
        self.tabla.column("#0", width=180, stretch=False)
        self.tabla.column("valor", width=480)
        self.tabla.pack(fill="both", expand=True)

        # Pestaña JSON
        self.texto = scrolledtext.ScrolledText(
            self.pestanas, font=("Consolas", 10), wrap="none"
        )
        self.pestanas.add(self.texto, text="JSON completo")

        # Botones
        barra = ttk.Frame(root)
        barra.pack(pady=(0, 6))
        self.btn_detectar = ttk.Button(
            barra, text="Detectar equipo", width=20, command=self.detectar
        )
        self.btn_detectar.pack(side="left", padx=6)
        self.btn_enviar = ttk.Button(
            barra, text="Enviar al sistema", width=20, command=self.enviar
        )
        self.btn_enviar.pack(side="left", padx=6)
        self.btn_copiar = ttk.Button(
            barra, text="Copiar JSON", width=14, command=self.copiar
        )
        self.btn_copiar.pack(side="left", padx=6)

        self.estado = tk.StringVar(value="Listo.")
        ttk.Label(
            root,
            textvariable=self.estado,
            relief="sunken",
            anchor="w",
            padding=(8, 3),
        ).pack(fill="x", side="bottom")

        self.detectar()

    # ---------- utilidades ----------

    def bloquear(self, bloqueado, mensaje=None):
        self.ocupado = bloqueado
        estado = "disabled" if bloqueado else "normal"
        for boton in (self.btn_detectar, self.btn_enviar, self.btn_copiar):
            boton.config(state=estado)
        if mensaje:
            self.estado.set(mensaje)

    def en_segundo_plano(self, tarea, al_terminar):
        """Ejecuta tarea() en un hilo y llama al_terminar(resultado, error)
        en el hilo de la interfaz."""

        def trabajo():
            try:
                resultado, error = tarea(), None
            except Exception as exc:  # noqa: BLE001
                resultado, error = None, exc
            self.root.after(0, lambda: al_terminar(resultado, error))

        threading.Thread(target=trabajo, daemon=True).start()

    def formatear(self, clave, valor):
        if valor in (None, "", []):
            return "(no disponible)"
        if clave == "ram_gb":
            return f"{valor} GB"
        if clave == "disco_gb":
            return f"{round(float(valor))} GB"
        return str(valor)

    def mostrar(self, datos):
        self.tabla.delete(*self.tabla.get_children())
        for etiqueta, clave in CAMPOS_RESUMEN:
            self.tabla.insert(
                "", "end", text=etiqueta,
                values=(self.formatear(clave, datos.get(clave)),),
            )
        self.texto.delete("1.0", "end")
        self.texto.insert(
            "end", json.dumps(datos, ensure_ascii=False, indent=2)
        )

    # ---------- acciones ----------

    def detectar(self):
        if self.ocupado:
            return
        self.bloquear(True, "Detectando equipo, puede tardar unos segundos...")

        def terminado(resultado, error):
            self.bloquear(False)
            if error:
                self.estado.set("Error al detectar el equipo.")
                messagebox.showerror("Error", str(error))
                return
            self.datos = resultado
            self.mostrar(resultado)
            aviso = ""
            if not resultado.get("bios_serial"):
                aviso = " (el equipo no reporta serial BIOS)"
            self.estado.set("Equipo detectado." + aviso)

        self.en_segundo_plano(recolectar, terminado)

    def enviar(self):
        if self.ocupado:
            return
        if not self.datos:
            messagebox.showwarning(
                "Sin datos", "Primero detecta el equipo."
            )
            return
        nombre = self.datos.get("hostname") or "este equipo"
        if not messagebox.askyesno(
            "Confirmar envío", f"¿Enviar los datos de {nombre} al sistema?"
        ):
            return

        self.bloquear(True, "Enviando al sistema...")
        datos = dict(self.datos)

        def terminado(resultado, error):
            self.bloquear(False)
            if error:
                self.estado.set("No se pudo enviar.")
                messagebox.showerror("Error", str(error))
                return
            mensaje = resultado.get("mensaje", "Información enviada")
            self.estado.set(mensaje)
            messagebox.showinfo("Correcto", mensaje)

        self.en_segundo_plano(lambda: enviar(datos), terminado)

    def copiar(self):
        contenido = self.texto.get("1.0", "end").strip()
        if not contenido:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(contenido)
        self.estado.set("JSON copiado al portapapeles.")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()