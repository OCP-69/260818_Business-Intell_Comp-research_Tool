<#
.SYNOPSIS
    Traegt einen wiederkehrenden Recherchelauf in die
    Windows-Aufgabenplanung ein.

.DESCRIPTION
    Legt eine geplante Aufgabe an, die Run-CintelProfile.ps1 mit einem
    festen Profil startet. Danach laeuft die Recherche selbsttaetig - Sie
    schauen nur noch in das Ergebnis.

    Die Aufgabe laeuft bewusst unter IHREM Windows-Konto und nur, wenn Sie
    angemeldet sind. Das ist keine Nachlaessigkeit, sondern notwendig: die
    Anmeldung der claude-Anwendung haengt am Benutzerkonto. Als SYSTEM
    gestartet faende die Aufgabe keine gueltige Anmeldung.

.PARAMETER ProfileName
    Name eines Profils aus config\profiles.yaml.

.PARAMETER Schedule
    Daily, Weekly oder Monthly.

.PARAMETER Time
    Uhrzeit im Format HH:mm, z.B. "06:30".

.PARAMETER DayOfWeek
    Nur bei Weekly, z.B. Monday.

.PARAMETER DayOfMonth
    Nur bei Monthly, 1 bis 28.

.PARAMETER Master
    Pfad zur Master-Tabelle. Ohne Angabe waehlt der Lauf die neueste
    Fassung aus data\outputs.

.PARAMETER TaskName
    Name der Aufgabe. Voreinstellung: "cintel - <Profil>".

.PARAMETER Remove
    Entfernt die Aufgabe wieder, statt sie anzulegen.

.EXAMPLE
    .\scripts\Register-CintelSchedule.ps1 -ProfileName bestand-luecken -Schedule Weekly -DayOfWeek Monday -Time 06:30

.EXAMPLE
    .\scripts\Register-CintelSchedule.ps1 -ProfileName lca-startups-dach -Schedule Monthly -DayOfMonth 1 -Time 07:00

.EXAMPLE
    .\scripts\Register-CintelSchedule.ps1 -ProfileName bestand-luecken -Remove
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ProfileName,

    [ValidateSet("Daily", "Weekly", "Monthly")]
    [string] $Schedule = "Weekly",

    [string] $Time = "06:30",

    [ValidateSet("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")]
    [string] $DayOfWeek = "Monday",

    [ValidateRange(1, 28)]
    [int] $DayOfMonth = 1,

    [string] $Master = "",

    [string] $TaskName = "",

    [switch] $Remove
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$Runner = Join-Path $ScriptDir "Run-CintelProfile.ps1"

if ([string]::IsNullOrWhiteSpace($TaskName)) {
    $TaskName = "cintel - $ProfileName"
}

# --- Entfernen ------------------------------------------------------------
if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-Output "Es gibt keine Aufgabe mit dem Namen '$TaskName'."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Aufgabe '$TaskName' wurde entfernt."
    exit 0
}

# --- Vorpruefungen --------------------------------------------------------
if (-not (Test-Path $Runner)) {
    Write-Error "Run-CintelProfile.ps1 nicht gefunden unter $Runner"
    exit 2
}

# Existiert das Profil ueberhaupt? Lieber jetzt scheitern als um 6:30 Uhr.
Push-Location $RepoRoot
try {
    $profileList = & py -m cintel profiles 2>&1 | Out-String
}
finally {
    Pop-Location
}
if ($profileList -notmatch [regex]::Escape($ProfileName)) {
    Write-Error @"
Das Profil '$ProfileName' steht nicht in config\profiles.yaml.
Verfuegbare Profile anzeigen mit:  py -m cintel profiles
"@
    exit 3
}

# --- Aufgabe zusammenbauen ------------------------------------------------
$argumentList = @(
    "-NoProfile"
    "-ExecutionPolicy Bypass"
    "-File `"$Runner`""
    "-ProfileName `"$ProfileName`""
)
if (-not [string]::IsNullOrWhiteSpace($Master)) {
    $argumentList += "-Master `"$Master`""
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($argumentList -join " ") `
    -WorkingDirectory $RepoRoot

switch ($Schedule) {
    "Daily" {
        $trigger = New-ScheduledTaskTrigger -Daily -At $Time
    }
    "Weekly" {
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $Time
    }
    "Monthly" {
        # New-ScheduledTaskTrigger kennt kein -Monthly. Der Umweg: woechentlich
        # anlegen und den Zeitplan anschliessend per XML auf monatlich stellen.
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At $Time
    }
}

# Nur laufen, wenn der Benutzer angemeldet ist - die claude-Anmeldung haengt
# am Benutzerkonto. S4U oder SYSTEM wuerden hier scheitern.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -MultipleInstances IgnoreNew

$description = "Automatischer Wettbewerbsrecherche-Lauf mit dem Profil '$ProfileName'. " +
               "Angelegt von Register-CintelSchedule.ps1."

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description $description `
    -Force | Out-Null

if ($Schedule -eq "Monthly") {
    Write-Warning @"
Monatlicher Zeitplan: Die Aufgabe wurde zunaechst woechentlich angelegt.
Bitte einmalig in der Aufgabenplanung umstellen:
  Aufgabenplanung oeffnen -> Aufgabe '$TaskName' -> Trigger -> Bearbeiten
  -> Monatlich -> Tag $DayOfMonth waehlen -> OK
"@
}

Write-Output ""
Write-Output "Aufgabe angelegt: $TaskName"
Write-Output "  Profil     : $ProfileName"
Write-Output "  Zeitplan   : $Schedule um $Time"
if ($Schedule -eq "Weekly") { Write-Output "  Wochentag  : $DayOfWeek" }
Write-Output "  Laeuft als : $env:USERDOMAIN\$env:USERNAME (nur bei Anmeldung)"
Write-Output "  Protokolle : $RepoRoot\data\logs"
Write-Output ""
Write-Output "Sofort testen, ohne auf den Termin zu warten:"
Write-Output "  Start-ScheduledTask -TaskName `"$TaskName`""
Write-Output ""
Write-Output "Wieder entfernen:"
Write-Output "  .\scripts\Register-CintelSchedule.ps1 -ProfileName $ProfileName -Remove"
