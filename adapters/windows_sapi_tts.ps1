param([switch]$SelfTest)

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Fail([string]$Message, [string]$Code = 'invalid_request') {
    [Console]::Error.WriteLine($Message)
    if (-not $script:SelfTest) {
        $provider = if ($script:Job -and $script:Job.adapter) { [string]$script:Job.adapter } else { 'windows-sapi' }
        $response = @{
            error = @{
                provider = $provider
                category = 'invalid_request'
                code = $Code
                retryable = $false
            }
        }
        [Console]::Out.Write(($response | ConvertTo-Json -Depth 4 -Compress))
    }
    exit 1
}

try {
    Add-Type -AssemblyName System.Speech

    if ($SelfTest) {
        $synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
        $sample = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName() + '.wav')
        try {
            $voice = $synth.GetInstalledVoices() | Where-Object { $_.Enabled } | Select-Object -First 1
            if (-not $voice) {
                throw 'No enabled Windows SAPI voice is installed.'
            }
            $synth.SelectVoice($voice.VoiceInfo.Name)
            $synth.SetOutputToWaveFile($sample)
            $synth.Speak('Windows SAPI adapter self-test.')
            $synth.SetOutputToNull()
            $header = [System.IO.File]::ReadAllBytes($sample)
            if ($header.Length -lt 12 -or [Text.Encoding]::ASCII.GetString($header, 0, 4) -ne 'RIFF' -or [Text.Encoding]::ASCII.GetString($header, 8, 4) -ne 'WAVE') {
                throw 'Windows SAPI did not create a valid WAV file.'
            }
        }
        finally {
            $synth.Dispose()
            if (Test-Path -LiteralPath $sample) {
                Remove-Item -LiteralPath $sample -Force
            }
        }
        [Console]::Out.WriteLine('Windows SAPI TTS self-test passed')
        exit 0
    }

    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw) -or $raw.Length -gt 1048576) {
        Fail 'Adapter input is empty or too large.'
    }
    $script:Job = ConvertFrom-Json -InputObject $raw -AsHashtable
    if ($script:Job.modality -ne 'tts' -or -not ($script:Job.outputs -is [array]) -or $script:Job.outputs.Count -ne 1) {
        Fail 'Expected one bounded TTS output.'
    }
    if (-not ($script:Job.prompt -is [string]) -or $script:Job.prompt.Trim().Length -eq 0 -or $script:Job.prompt.Length -gt 4000) {
        Fail 'TTS prompt must contain 1 to 4000 characters.'
    }

    $target = [string]$script:Job.outputs[0]
    if ([System.IO.Path]::GetExtension($target) -ine '.wav') {
        Fail 'Windows SAPI output must use the WAV format.'
    }
    $parameters = $script:Job.parameters
    if (-not ($parameters -is [System.Collections.IDictionary])) {
        Fail 'TTS parameters are missing.'
    }
    $unknown = @($parameters.Keys | Where-Object { $_ -notin @('voice_name', 'rate', 'volume') })
    if ($unknown.Count -gt 0) {
        Fail 'Unsupported Windows SAPI parameters were supplied.'
    }
    $voiceName = $parameters['voice_name']
    if (-not ($voiceName -is [string]) -or [string]::IsNullOrWhiteSpace($voiceName)) {
        Fail 'Select an installed voice with the voice_name parameter.'
    }
    $rate = if ($parameters.Contains('rate')) { $parameters['rate'] } else { 0 }
    $volume = if ($parameters.Contains('volume')) { $parameters['volume'] } else { 100 }
    if (($rate -isnot [int] -and $rate -isnot [long]) -or $rate -lt -10 -or $rate -gt 10) {
        Fail 'The rate parameter must be an integer from -10 to 10.'
    }
    if (($volume -isnot [int] -and $volume -isnot [long]) -or $volume -lt 0 -or $volume -gt 100) {
        Fail 'The volume parameter must be an integer from 0 to 100.'
    }
    $rate = [int]$rate
    $volume = [int]$volume

    if (-not ($script:Job.output_root -is [string]) -or [string]::IsNullOrWhiteSpace($script:Job.output_root)) {
        Fail 'Adapter output directory is missing.'
    }
    $outputRoot = [System.IO.Path]::GetFullPath($script:Job.output_root)
    if (-not (Test-Path -LiteralPath $outputRoot -PathType Container)) {
        Fail 'Adapter output directory does not exist.'
    }
    $outputPath = Join-Path $outputRoot ([System.IO.Path]::GetRandomFileName() + '.wav')
    $synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
    try {
        $synth.SelectVoice($voiceName)
        $synth.Rate = $rate
        $synth.Volume = $volume
        $synth.SetOutputToWaveFile($outputPath)
        $synth.Speak($script:Job.prompt)
        $synth.SetOutputToNull()
    }
    finally {
        $synth.Dispose()
    }
    if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf) -or (Get-Item -LiteralPath $outputPath).Length -eq 0) {
        throw 'Windows SAPI did not produce a WAV file.'
    }

    $response = @{ outputs = @(@{ target = $target; source = $outputPath }) }
    [Console]::Out.Write(($response | ConvertTo-Json -Depth 4 -Compress))
}
catch {
    if ($outputPath -and (Test-Path -LiteralPath $outputPath)) {
        Remove-Item -LiteralPath $outputPath -Force -ErrorAction SilentlyContinue
    }
    if ($script:SelfTest) {
        [Console]::Error.WriteLine($_.Exception.Message)
        exit 1
    }
    $provider = if ($script:Job -and $script:Job.adapter) { [string]$script:Job.adapter } else { 'windows-sapi' }
    $response = @{
        error = @{
            provider = $provider
            category = 'provider_response'
            code = 'synthesis_failed'
            retryable = $false
        }
    }
    [Console]::Error.WriteLine('Windows SAPI synthesis failed.')
    [Console]::Out.Write(($response | ConvertTo-Json -Depth 4 -Compress))
    exit 1
}
