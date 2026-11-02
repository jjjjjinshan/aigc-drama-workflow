# EP001 成片编排（合成示例）

审片记录：对白保持可辨，第二镜切入前保留半秒反应空间。以下来源 ID 为合成样例，需替换成当前项目已核验的成功运行。

```json
{
  "schema_version": "1.0",
  "episode_id": "EP001",
  "review_status": "approved",
  "frame": {
    "width": 1080,
    "height": 1920,
    "fps_num": 30,
    "fps_den": 1
  },
  "subtitle_mode": "sidecar",
  "voice_source": {
    "sheet": "episodes/EP001/voice-record-sheet.jsonl",
    "index": "episodes/EP001/screenplay-index.jsonl",
    "screenplay": "episodes/EP001/剧本.md"
  },
  "video_clips": [
    {
      "clip_id": "CLIP-EP001-001",
      "source": {
        "job_id": "EP001-SHOT-001",
        "run_id": "20261102T120000-a1b2c3d4",
        "path": "dist/EP001/video/SHOT-EP001-001.mp4"
      },
      "source_in_ms": 0,
      "duration_ms": 9000
    },
    {
      "clip_id": "CLIP-EP001-002",
      "source": {
        "job_id": "EP001-SHOT-002",
        "run_id": "20261102T120100-b2c3d4e5",
        "path": "dist/EP001/video/SHOT-EP001-002.mp4"
      },
      "source_in_ms": 500,
      "duration_ms": 10500
    }
  ],
  "audio_clips": [
    {
      "clip_id": "AUDIO-EP001-DIALOGUE-001",
      "role": "dialogue",
      "voice_line_id": "VLINE-EP001-SC001-003",
      "source": {
        "job_id": "EP001-TTS-003",
        "run_id": "20261102T120200-c3d4e5f6",
        "path": "dist/EP001/audio/VLINE-EP001-SC001-003.wav"
      },
      "source_in_ms": 0,
      "timeline_start_ms": 3200,
      "duration_ms": 1800,
      "gain_db": 0,
      "fade_in_ms": 0,
      "fade_out_ms": 100
    },
    {
      "clip_id": "AUDIO-EP001-AMBIENCE-001",
      "role": "ambience",
      "source": {
        "job_id": "EP001-SFX-001",
        "run_id": "20261102T120300-d4e5f6a7",
        "path": "dist/EP001/audio/platform-hum.wav"
      },
      "source_in_ms": 0,
      "timeline_start_ms": 0,
      "duration_ms": 19700,
      "gain_db": -24,
      "fade_in_ms": 300,
      "fade_out_ms": 500
    }
  ],
  "captions": [
    {
      "voice_line_id": "VLINE-EP001-SC001-003",
      "timeline_start_ms": 3200,
      "duration_ms": 1800
    }
  ],
  "output": {
    "filename": "EP001-master.mp4"
  }
}
```
