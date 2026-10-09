param(
    [string]$Carpeta = [Environment]::GetFolderPath("Desktop")
)

$VersionRecolector = "1.2-archivo"
$avisos = New-Object System.Collections.ArrayList

# ---------- utilidades ----------

function Valor($x) {
    if ($null -eq $x) { return $null }
    $t = ([string]$x).Trim()
    if ($t -eq "") { return $null }
    return $t
}

# Seriales genericos que ponen algunos fabricantes: se tratan como "sin serial"
function Serial($x) {
    $t = Valor $x
    if (-not $t) { return $null }
    if ($t -match '^(to be filled|default string|system serial|none$|n/a|not specified|0+$|123456789|o\.e\.m)') { return $null }
    return $t
}

# Consulta WMI con respaldo (CIM -> WMI clasico). Si falla, devuelve $null.
function Consultar($Clase, $Filtro) {
    try {
        if ($Filtro) { return @(Get-CimInstance -ClassName $Clase -Filter $Filtro -ErrorAction Stop) }
        return @(Get-CimInstance -ClassName $Clase -ErrorAction Stop)
    } catch {
        try {
            if ($Filtro) { return @(Get-WmiObject -Class $Clase -Filter $Filtro -ErrorAction Stop) }
            return @(Get-WmiObject -Class $Clase -ErrorAction Stop)
        } catch {
            [void]$avisos.Add("No se pudo leer $Clase`: $($_.Exception.Message)")
            return $null
        }
    }
}

function Primero($lista) {
    if ($lista -and $lista.Count -gt 0) { return $lista[0] }
    return $null
}

# ---------- recoleccion ----------

$cs    = Primero (Consultar "Win32_ComputerSystem")
$bios  = Primero (Consultar "Win32_BIOS")
$prod  = Primero (Consultar "Win32_ComputerSystemProduct")
$cpu   = Primero (Consultar "Win32_Processor")
$os    = Primero (Consultar "Win32_OperatingSystem")
$discos = Consultar "Win32_LogicalDisk" "DriveType=3"
$redes  = Consultar "Win32_NetworkAdapterConfiguration" "IPEnabled=True"

# Disco total (suma de discos locales)
$discoGb = $null
if ($discos) {
    $suma = 0
    foreach ($d in $discos) { if ($d.Size) { $suma += [double]$d.Size } }
    if ($suma -gt 0) { $discoGb = [math]::Round($suma / 1GB, 1) }
}

# Red: prefiere el adaptador con puerta de enlace y una IPv4
$ip = $null; $mac = $null
if ($redes) {
    $candidatos = @($redes | Where-Object { $_.IPAddress })
    $elegido = $candidatos | Where-Object { $_.DefaultIPGateway } | Select-Object -First 1
    if (-not $elegido) { $elegido = Primero $candidatos }
    if ($elegido) {
        $ip  = ($elegido.IPAddress | Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' } | Select-Object -First 1)
        $mac = Valor $elegido.MACAddress
    }
}

# Respaldos si WMI no respondio
$arquitectura = if ($os) { Valor $os.OSArchitecture } else { $null }
if (-not $arquitectura) { $arquitectura = $env:PROCESSOR_ARCHITECTURE }

$sistema = if ($os) { Valor $os.Caption } else { $null }
if (-not $sistema) { $sistema = [Environment]::OSVersion.VersionString }

$versionSo = if ($os) { Valor $os.Version } else { $null }
if (-not $versionSo) { $versionSo = [Environment]::OSVersion.Version.ToString() }

$ramGb = $null
if ($cs -and $cs.TotalPhysicalMemory) { $ramGb = [math]::Round([double]$cs.TotalPhysicalMemory / 1GB, 1) }

$usuario = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }

$datos = [ordered]@{
    hostname           = $env:COMPUTERNAME
    usuario_windows    = $usuario
    bios_serial        = if ($bios) { Serial $bios.SerialNumber } else { $null }
    uuid_equipo        = if ($prod) { Valor $prod.UUID } else { $null }
    marca              = if ($cs)   { Valor $cs.Manufacturer } else { $null }
    modelo             = if ($cs)   { Valor $cs.Model } else { $null }
    procesador         = if ($cpu)  { Valor $cpu.Name } else { $null }
    ram_gb             = $ramGb
    disco_gb           = $discoGb
    sistema_operativo  = $sistema
    version_sistema    = $versionSo
    arquitectura       = $arquitectura
    direccion_ip       = $ip
    direccion_mac      = $mac
    dominio            = if ($cs)   { Valor $cs.Domain } else { $null }
    version_recolector = $VersionRecolector
    fecha_generado     = (Get-Date).ToUniversalTime().ToString("o")
}

$json = $datos | ConvertTo-Json

# ---------- guardado con carpetas de respaldo ----------

$nombre = "$($env:COMPUTERNAME).json"
$candidatas = @(
    $Carpeta,
    [Environment]::GetFolderPath("Desktop"),
    [Environment]::GetFolderPath("MyDocuments"),
    $env:USERPROFILE,
    $env:TEMP
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique

$utf8 = New-Object System.Text.UTF8Encoding($false)   # UTF-8 sin BOM
$archivo = $null
$ultimoError = $null

foreach ($dir in $candidatas) {
    $ruta = Join-Path $dir $nombre
    try {
        [System.IO.File]::WriteAllText($ruta, $json, $utf8)
        $archivo = $ruta
        break
    } catch {
        $ultimoError = $_.Exception.Message
    }
}

if (-not $archivo) {
    Write-Host "No se pudo guardar el archivo en ninguna carpeta." -ForegroundColor Red
    Write-Host "Ultimo error: $ultimoError" -ForegroundColor Red
    exit 1
}

# ---------- resultado ----------

Write-Host ""
Write-Host "Archivo generado: $archivo" -ForegroundColor Green
if ($archivo -notlike "$Carpeta*") {
    Write-Host "(La carpeta pedida no permitio escribir; se uso una alterna.)" -ForegroundColor Yellow
}
if (-not $datos.bios_serial) {
    Write-Host "Aviso: este equipo no reporta un serial de BIOS valido." -ForegroundColor Yellow
}
foreach ($a in $avisos) { Write-Host "Aviso: $a" -ForegroundColor Yellow }
Write-Host "Subelo en Equipos detectados > Subir archivos."

# Abre la carpeta con el archivo seleccionado
try { Start-Process explorer.exe "/select,`"$archivo`"" } catch { }