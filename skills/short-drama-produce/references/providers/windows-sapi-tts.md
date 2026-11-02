# Windows SAPI TTS Adapter

This optional adapter creates one local WAV utterance through an installed Windows Speech API voice. It sends no text or audio to an external service and has no provider usage charge.

## Requirements

- Windows with PowerShell 7 (`pwsh`) and the .NET `System.Speech` assembly.
- An enabled Windows SAPI voice installed for the account running the adapter.
- A TTS job with one `.wav` output and an explicitly selected installed voice.

## Adapter profile

Keep this configuration outside the creator project. Replace the script path with its absolute path on the production machine:

```json
{
  "adapters": {
    "windows-sapi": {
      "command": ["pwsh", "-NoProfile", "-File", "C:/aigc-drama-workflow/adapters/windows_sapi_tts.ps1"],
      "timeout_seconds": 120
    }
  }
}
```

Set the job's `adapter` to `windows-sapi`. Its confirmed `parameters` may contain:

| Parameter | Type | Range | Default |
|---|---|---:|---:|
| `voice_name` | string | Exact installed voice name; required | — |
| `rate` | integer | -10 to 10 | 0 |
| `volume` | integer | 0 to 100 | 100 |

The adapter rejects unsupported parameters and non-WAV outputs. It speaks the exact confirmed `prompt`; it does not apply SSML or reinterpret performance notes.

## Quality boundary

SAPI is a local integration baseline for testing one-line generation, source binding, subtitles, and mixdown. It does not provide production-grade emotional acting, voice cloning, or cross-platform behavior. A technically valid WAV still requires creator listening and approval before it counts as usable dialogue.

Run the adapter's machine check with:

```powershell
pwsh -NoProfile -File <script-path> -SelfTest
```
