param(
    [string]$Token,
    [string]$Servidor
)

$ErrorActionPreference = "Stop"
$VersionRecolector = "1.0-ps1"

# Servidor: parametro > variable de entorno > direccion inyectada al descargar
if (-not $Servidor) { $Servidor = $env:INVENTARIO_API_URL }
if (-not $Servidor) { $Servidor = "__SERVIDOR__" }

# Token: parametro > variable de entorno (nunca se pide por pantalla)
if (-not $Token) { $Token = $env:INVENTARIO_RECOLECTOR_TOKEN }

if ([string]::IsNullOrWhiteSpace($Token)) {
    Write-Host "Falta el token. Define la variable INVENTARIO_RECOLECTOR_TOKEN (setx) y abre una terminal nueva, o usa -Token." -ForegroundColor Red
    exit 1
}

$Servidor = $Servidor.Trim().TrimEnd("/")
$Token = $Token.Trim()

Write-Host "Servidor: $Servidor | Largo del token: $($Token.Length)" -ForegroundColor Cyan

function Valor($x) { if ($null -eq $x) { return $null } return ([string]$x).Trim() }

$cs    = Get-CimInstance Win32_ComputerSystem
$bios  = Get-CimInstance Win32_BIOS
$prod  = Get-CimInstance Win32_ComputerSystemProduct
$cpu   = Get-CimInstance Win32_Processor | Select-Object -First 1
$os    = Get-CimInstance Win32_OperatingSystem
$disco = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
         Measure-Object -Property Size -Sum
$red   = Get-CimInstance Win32_NetworkAdapterConfiguration |
         Where-Object { $_.IPEnabled -and $_.IPAddress } | Select-Object -First 1

$cuerpo = @{
    hostname           = $env:COMPUTERNAME
    usuario_windows    = "$env:USERDOMAIN\$env:USERNAME"
    bios_serial        = Valor $bios.SerialNumber
    uuid_equipo        = Valor $prod.UUID
    marca              = Valor $cs.Manufacturer
    modelo             = Valor $cs.Model
    procesador         = Valor $cpu.Name
    ram_gb             = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
    disco_gb           = [math]::Round($disco.Sum / 1GB, 1)
    sistema_operativo  = Valor $os.Caption
    version_sistema    = Valor $os.Version
    arquitectura       = Valor $os.OSArchitecture
    direccion_ip       = if ($red) { $red.IPAddress[0] } else { $null }
    direccion_mac      = if ($red) { $red.MACAddress } else { $null }
    dominio            = Valor $cs.Domain
    version_recolector = $VersionRecolector
} | ConvertTo-Json

try {
    $resp = Invoke-RestMethod -Uri "$Servidor/api/recolector/equipos" `
        -Method Post -ContentType "application/json; charset=utf-8" `
        -Headers @{
            "X-Recolector-Token" = $Token
            "Authorization"      = "Bearer $Token"
        } `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($cuerpo))
    Write-Host "OK: $($resp.mensaje) (resultado: $($resp.resultado))" -ForegroundColor Green
}
catch {
    Write-Host "Error enviando el equipo: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Response) {
        try {
            $lector = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            Write-Host "Respuesta: $($lector.ReadToEnd())" -ForegroundColor Red
        } catch { }
    }
    exit 1
}