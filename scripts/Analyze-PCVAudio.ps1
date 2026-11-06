param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [string]$Culture
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech

$file = Get-Item -LiteralPath $Path
if ($file.PSIsContainer -or $file.Extension -ne '.wav') {
    throw 'Windows speech recognition requires a WAV file.'
}
$installed = @([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers())
if ($Culture) {
    $info = $installed | Where-Object { $_.Culture.Name -eq $Culture } | Select-Object -First 1
} else {
    $preferred = [Globalization.CultureInfo]::CurrentUICulture.Name
    $info = $installed | Where-Object { $_.Culture.Name -eq $preferred } | Select-Object -First 1
    if (-not $info) { $info = $installed | Select-Object -First 1 }
}
if (-not $info) {
    $available = ($installed | ForEach-Object { $_.Culture.Name }) -join ', '
    throw "No matching Windows speech recognizer. Installed cultures: $available"
}

$engine = [System.Speech.Recognition.SpeechRecognitionEngine]::new($info)
$recognized = "pcv-recognized-$PID"
$completed = "pcv-completed-$PID"
try {
    $engine.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
    $engine.SetInputToWaveFile($file.FullName)
    Register-ObjectEvent $engine SpeechRecognized -SourceIdentifier $recognized | Out-Null
    Register-ObjectEvent $engine RecognizeCompleted -SourceIdentifier $completed | Out-Null
    $engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
    $done = Wait-Event -SourceIdentifier $completed -Timeout 45
    if (-not $done) {
        $engine.RecognizeAsyncCancel()
        throw 'Windows speech recognition timed out.'
    }
    if ($done.SourceEventArgs.Error) { throw $done.SourceEventArgs.Error }

    $segments = @(Get-Event -SourceIdentifier $recognized -ErrorAction SilentlyContinue | ForEach-Object {
        $result = $_.SourceEventArgs.Result
        if ($result.Audio -and $result.Text) {
            $start = $result.Audio.AudioPosition.TotalSeconds
            [pscustomobject]@{
                text = $result.Text
                start_seconds = $start
                end_seconds = $start + $result.Audio.Duration.TotalSeconds
            }
        }
    } | Sort-Object start_seconds)
    [Console]::WriteLine((ConvertTo-Json -InputObject $segments -Compress))
} finally {
    foreach ($eventName in @($recognized, $completed)) {
        Unregister-Event -SourceIdentifier $eventName -ErrorAction SilentlyContinue
        Remove-Event -SourceIdentifier $eventName -ErrorAction SilentlyContinue
    }
    $engine.Dispose()
}
