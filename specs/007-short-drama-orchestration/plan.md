# Implementation Plan: Short Drama Orchestration

Add typed project/shot contracts and a drama orchestrator built on the shared workflow runner and media boundary. Existing tracked runs or inline local workflow results become shot sources. FFmpeg normalizes each shot into a content-addressed cache, assembles cuts/fades, pads/trims supplied audio, optionally burns subtitles, and produces a parent manifest with source hashes and nested MLflow tags. Resume accepts only a matching fingerprint and output hash.

Constitution gates pass: cloud output must first be imported; credentials never enter project files; source licenses propagate; all media stays local until reviewed export.

