# dist/ —— 渲染产物目录

`short-drama-produce` 通过四步硬闸门产出的媒体落在这里，按集分目录：

```text
dist/
└── EP001/
    ├── images/     图片提示词条目渲染出的资产图
    ├── video/      逐镜视频片段
    └── audio/      TTS 配音与时间线音乐
```

- 目录内容是二进制大文件，已被 `.gitignore` 忽略（本文件除外）。
- 生产 job 的 `outputs` 必须写成 `dist/<EP>/...`；旧布局 `production/` 与
  `episodes|剧集/<EP>/制作成果/` 仍然可读。
- 这里只放**渲染产物**。成片与交付包由创作者剪辑后另行归档，工作流不代劳。
