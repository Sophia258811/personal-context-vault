param(
    [Parameter(Mandatory)][string]$Path,
    [string]$Culture
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } |
    Select-Object -First 1

function Wait-WinRtResult($Operation, [type]$ResultType) {
    $task = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}

$engine = if ($Culture) {
    $language = [Windows.Globalization.Language]::new($Culture)
    [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
} else {
    [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($null -eq $engine) {
    $available = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages |
        ForEach-Object { $_.LanguageTag }
    throw "Windows OCR language unavailable. Installed: $($available -join ', ')"
}

$file = [System.IO.File]::OpenRead((Resolve-Path -LiteralPath $Path).ProviderPath)
$stream = $null
$bitmap = $null
try {
    $stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($file)
    $decoder = Wait-WinRtResult ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) (
        [Windows.Graphics.Imaging.BitmapDecoder]
    )
    $width = $decoder.PixelWidth
    $height = $decoder.PixelHeight
    $maximum = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($width -gt $maximum -or $height -gt $maximum) {
        throw "Image exceeds the Windows OCR limit of $maximum pixels per side."
    }

    # Decode without applying EXIF rotation so the boxes address the stored pixels.
    $decode = $decoder.GetSoftwareBitmapAsync(
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore,
        [Windows.Graphics.Imaging.BitmapTransform]::new(),
        [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
        [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
    )
    $bitmap = Wait-WinRtResult $decode ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Wait-WinRtResult ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = @(
        foreach ($line in $result.Lines) {
            if (-not $line.Text.Trim() -or $line.Words.Count -eq 0) { continue }
            $left = [double]$width
            $top = [double]$height
            $right = 0.0
            $bottom = 0.0
            foreach ($word in $line.Words) {
                $rect = $word.BoundingRect
                $left = [Math]::Min($left, $rect.X)
                $top = [Math]::Min($top, $rect.Y)
                $right = [Math]::Max($right, $rect.X + $rect.Width)
                $bottom = [Math]::Max($bottom, $rect.Y + $rect.Height)
            }
            @{
                text = $line.Text
                box = @(
                    [Math]::Max(0.0, $left / $width)
                    [Math]::Max(0.0, $top / $height)
                    [Math]::Min(1.0, $right / $width)
                    [Math]::Min(1.0, $bottom / $height)
                )
            }
        }
    )
    ConvertTo-Json -InputObject $lines -Depth 4 -Compress
} finally {
    if ($null -ne $bitmap) { $bitmap.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
    $file.Dispose()
}
