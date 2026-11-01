"""Offline Windows OCR without Python-version-specific native dependencies."""
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import tempfile


_PRELUDE = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] > $null
"""
_RECOGNIZE = _PRELUDE + r"""
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] > $null
[Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime] > $null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime] > $null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType=WindowsRuntime] > $null
[Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime] > $null
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation' + [char]96 + '1'
} | Select-Object -First 1
function Await($operation, $resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'No Windows OCR language is installed.' }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($payload.image_path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        if ($bitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
            $bitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) {
            throw 'Image exceeds Windows OCR dimensions.'
        }
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines = @($result.Lines | ForEach-Object {
            $words = @($_.Words)
            if ($words.Count -gt 0) {
                $left = ($words | ForEach-Object { $_.BoundingRect.X } | Measure-Object -Minimum).Minimum
                $top = ($words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum
                $right = ($words | ForEach-Object { $_.BoundingRect.X + $_.BoundingRect.Width } | Measure-Object -Maximum).Maximum
                $bottom = ($words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum
                @{text=$_.Text; bbox=@($left, $top, $right, $bottom)}
            }
        })
        ConvertTo-Json -InputObject $lines -Depth 5 -Compress
    } finally { if ($null -ne $bitmap) { $bitmap.Dispose() } }
} finally { $stream.Dispose() }
"""


def _invoke(script, payload=None):
    import base64
    executable = Path(os.environ.get("SystemRoot", "C:/Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe")
    result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand",
        base64.b64encode(script.encode("utf-16le")).decode("ascii")],
        input=json.dumps(payload or {}), capture_output=True, text=True, encoding="utf-8",
        timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode:
        raise RuntimeError("Windows OCR could not recognize the supplied image.")
    return json.loads(result.stdout.lstrip("\ufeff"))


class WindowsOCREngine:
    @staticmethod
    @lru_cache(maxsize=1)
    def available():
        if os.name != "nt":
            return False
        try:
            return bool(_invoke(_PRELUDE +
                "ConvertTo-Json -InputObject @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | "
                "ForEach-Object { $_.LanguageTag }) -Compress"))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return False

    def __call__(self, image):
        with tempfile.TemporaryDirectory(prefix="crossbot-ocr-") as folder:
            path = Path(folder) / "source.png"
            image.save(path, format="PNG")
            lines = _invoke(_RECOGNIZE, {"image_path": str(path)})
        rows = []
        for line in lines:
            left, top, right, bottom = line["bbox"]
            rows.append(([(left, top), (right, top), (right, bottom), (left, bottom)],
                         line["text"], None))
        return rows, None
