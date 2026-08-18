<#
.SYNOPSIS
    Startet einen Recherchelauf mit einem vorkonfigurierten Profil und
    schreibt ein Protokoll.

.DESCRIPTION
    Dieses Skript ist der Einstiegspunkt fuer zeitgesteuerte Laeufe. Es
    wird von der Windows-Aufgabenplanung aufgerufen, laesst sich aber auch
    von Hand starten.

    Es kuemmert sich um die Dinge, an die man bei einem unbeaufsichtigten
    Lauf denken muss:
      - in das richtige Verzeichnis wechseln
      - pruefen, ob die Master-Tabelle ueberhaupt erreichbar ist
        (Laufwerk H: ist Google Drive und kann getrennt sein)
      - die Ausgabe vollstaendig in eine Protokolldatei schreiben
      - alte Protokolle aufraeumen
      - einen sinnvollen Exit-Code zurueckgeben

.PARAMETER ProfileName
    Name eines Profils aus config\profiles.yaml.

.PARAMETER Master
    Pfad zur Master-Tabelle, die als Grundlage dient.

.PARAMETER Version
    Versionsnummer der Ergebnisdatei, z.B. "2.4". Ohne Angabe wird die
    Nebenversion automatisch hochgezaehlt.

.PARAMETER DryRun
    Alles rechnen, aber keine Excel-Datei schreiben.

.PARAMETER KeepLogs
    Wie viele Protokolldateien aufbewahrt werden. Voreinstellung: 30.

.EXAMPLE
    .\scripts\Run-CintelProfile.ps1 -ProfileName bestand-luecken

.EXAMPLE
    .\scripts\Run-CintelProfile.ps1 -ProfileName lca-startups-dach -Version 2.5

.NOTES
    Der Lauf braucht die angemeldete claude-Anwendung. Die Anmeldung haengt
    am Windows-Benutzerkonto, deshalb muss eine geplante Aufgabe unter
    demselben Konto laufen - nicht als SYSTEM.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ProfileName,

    [string] $Master = "",

    [string] $Version = "",

    [switch] $DryRun,

    [int] $KeepLogs = 30
)

$ErrorActionPreference = "Stop"

# --- Projektwurzel bestimmen (dieses Skript liegt in scripts\) -------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

# --- Protokolldatei vorbereiten -------------------------------------------
$LogDir = Join-Path $RepoRoot "data\logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir ("{0}_{1}.log" -f $ProfileName, $Stamp)

function Write-Log {
    param([string] $Message)
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Write-Log "=== cintel-Lauf gestartet ==="
Write-Log "Profil       : $ProfileName"
Write-Log "Projektordner: $RepoRoot"
Write-Log "Benutzer     : $env:USERNAME"

# --- Master-Tabelle bestimmen und pruefen ---------------------------------
if ([string]::IsNullOrWhiteSpace($Master)) {
    # Voreinstellung: die zuletzt bereinigte Fassung im Ausgabeordner.
    $candidates = Get-ChildItem -Path (Join-Path $RepoRoot "data\outputs") `
        -Filter "Competitive_Intel_Master_DB_*.xlsx" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    if ($candidates -and $candidates.Count -gt 0) {
        $Master = $candidates[0].FullName
        Write-Log "Master automatisch gewaehlt: $($candidates[0].Name)"
    }
    else {
        Write-Log "FEHLER: Keine Master-Tabelle angegeben und keine im Ausgabeordner gefunden."
        Write-Log '        Bitte -Master "<pfad zur xlsx>" angeben.'
        exit 2
    }
}

if (-not (Test-Path -LiteralPath $Master)) {
    Write-Log "FEHLER: Master-Tabelle nicht erreichbar: $Master"
    Write-Log "        Liegt sie auf H:? Dann ist Google Drive vermutlich nicht verbunden."
    Write-Log "        Google Drive starten, kurz warten, Lauf erneut anstossen."
    exit 3
}
Write-Log "Master       : $Master"

# --- Voraussetzungen pruefen ----------------------------------------------
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($null -eq $claude) {
    Write-Log "FEHLER: Die claude-Anwendung wurde nicht gefunden."
    Write-Log "        Ohne sie kann nicht ausgewertet werden."
    exit 4
}

# --- Befehl zusammenbauen -------------------------------------------------
$cintelArgs = @("-m", "cintel", "run", "--master", $Master, "--profile", $ProfileName)
if (-not [string]::IsNullOrWhiteSpace($Version)) {
    $cintelArgs += @("--version", $Version)
}
if ($DryRun) {
    $cintelArgs += "--dry-run"
    Write-Log "Probelauf: es wird keine Excel-Datei geschrieben."
}
Write-Log ("Befehl       : py " + ($cintelArgs -join " "))
Write-Log "--------------------------------------------------------------"

# Umlaute in der Ausgabe korrekt behandeln.
$env:PYTHONIOENCODING = "utf-8"

# --- Lauf ausfuehren, Ausgabe vollstaendig protokollieren -----------------
$exitCode = 0
try {
    & py @cintelArgs 2>&1 | ForEach-Object {
        $text = $_.ToString()
        Write-Output $text
        Add-Content -Path $LogFile -Value $text -Encoding UTF8
    }
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
}
catch {
    Write-Log "AUSNAHME: $($_.Exception.Message)"
    $exitCode = 1
}

Write-Log "--------------------------------------------------------------"
if ($exitCode -eq 0) {
    Write-Log "Lauf erfolgreich beendet."
}
else {
    Write-Log "Lauf mit Fehlercode $exitCode beendet."
}
Write-Log "Protokoll: $LogFile"

# --- Alte Protokolle aufraeumen -------------------------------------------
$old = Get-ChildItem -Path $LogDir -Filter "*.log" | Sort-Object LastWriteTime -Descending
if ($old.Count -gt $KeepLogs) {
    $old | Select-Object -Skip $KeepLogs | ForEach-Object {
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
    }
    Write-Log ("Alte Protokolle entfernt, {0} behalten." -f $KeepLogs)
}

exit $exitCode
