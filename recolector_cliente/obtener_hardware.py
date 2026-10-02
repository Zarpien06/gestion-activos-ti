import getpass
import json
import platform
import socket
import subprocess
import uuid


def powershell_json(comando):
    proceso = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", comando],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
    )
    if proceso.returncode != 0:
        raise RuntimeError(proceso.stderr.strip() or "Fallo PowerShell")
    texto = proceso.stdout.strip()
    return json.loads(texto) if texto else None


def primer_valor(valor):
    if isinstance(valor, list):
        return valor[0] if valor else {}
    return valor or {}


def obtener_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def recolectar():
    sistema = primer_valor(powershell_json(
        "Get-CimInstance Win32_ComputerSystem | "
        "Select-Object Name,Manufacturer,Model,Domain,UserName,TotalPhysicalMemory | ConvertTo-Json -Compress"
    ))
    bios = primer_valor(powershell_json(
        "Get-CimInstance Win32_BIOS | Select-Object SerialNumber,SMBIOSBIOSVersion | ConvertTo-Json -Compress"
    ))
    producto = primer_valor(powershell_json(
        "Get-CimInstance Win32_ComputerSystemProduct | Select-Object UUID,IdentifyingNumber | ConvertTo-Json -Compress"
    ))
    procesador = primer_valor(powershell_json(
        "Get-CimInstance Win32_Processor | Select-Object Name | ConvertTo-Json -Compress"
    ))
    so = primer_valor(powershell_json(
        "Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture | ConvertTo-Json -Compress"
    ))
    discos = powershell_json(
        "Get-CimInstance Win32_DiskDrive | Select-Object Model,SerialNumber,Size,MediaType | ConvertTo-Json -Compress"
    ) or []
    if isinstance(discos, dict): discos=[discos]
    adaptadores = powershell_json(
        "Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled} | "
        "Select-Object Description,MACAddress,IPAddress | ConvertTo-Json -Compress"
    ) or []
    if isinstance(adaptadores, dict): adaptadores=[adaptadores]
    ram_bytes = int(sistema.get("TotalPhysicalMemory") or 0)
    disco_bytes = sum(int(d.get("Size") or 0) for d in discos)
    mac = next((a.get("MACAddress") for a in adaptadores if a.get("MACAddress")), None)
    return {
        "hostname": sistema.get("Name") or socket.gethostname(),
        "usuario_windows": sistema.get("UserName") or getpass.getuser(),
        "dominio": sistema.get("Domain"),
        "marca": sistema.get("Manufacturer"),
        "modelo": sistema.get("Model"),
        "bios_serial": (bios.get("SerialNumber") or "").strip() or None,
        "uuid_equipo": producto.get("UUID"),
        "procesador": procesador.get("Name"),
        "ram_gb": round(ram_bytes / 1073741824, 2),
        "disco_gb": round(disco_bytes / 1073741824, 2),
        "sistema_operativo": so.get("Caption"),
        "version_sistema": so.get("Version"),
        "arquitectura": so.get("OSArchitecture") or platform.machine(),
        "direccion_ip": obtener_ip(),
        "direccion_mac": mac or ':'.join(f'{(uuid.getnode() >> i) & 0xff:02x}' for i in range(40,-1,-8)),
        "discos": discos,
        "adaptadores": adaptadores,
    }
