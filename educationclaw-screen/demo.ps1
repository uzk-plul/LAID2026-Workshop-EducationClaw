# Cycles messages and moods into message.json so you can watch the face react.
# Run robotface in one terminal, this script in another.
param([string]$Path = "message.json", [int]$DelaySeconds = 3)

$frames = @(
    @{ message = "Hello!";        mood = "neutral" },
    @{ message = "Build passed";  mood = "happy"   },
    @{ message = "Deploying...";  mood = "neutral" },
    @{ message = "Tests failed";  mood = "sad"     },
    @{ message = "Fixed it";      mood = "happy"   }
)

Write-Host "Writing to $Path every $DelaySeconds s. Ctrl+C to stop."
while ($true) {
    foreach ($frame in $frames) {
        $frame | ConvertTo-Json | Set-Content -Path $Path -Encoding utf8
        Write-Host "  -> $($frame.mood): $($frame.message)"
        Start-Sleep -Seconds $DelaySeconds
    }
}
