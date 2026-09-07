# RLE export trial

The normal editor export stays raw RGB for compatibility during this trial.
In EFFECT BANK / EXPORT, use **Convert…** under Save bank to save the entire
mapped bank as compressed `effect_data.h`. It opens the normal save browser,
checks the decoded frames and reports raw/stored size. Save canvas edits into
the bank first if those should be included in the conversion.
To convert an existing bank (including embedded editable projects):

```powershell
$env:PYTHONPATH = 'F:\Projects\CnC-Light-Editor\src'
.\.venv\Scripts\python.exe -m cnc_light_editor.compress_bank INPUT.h OUTPUT.h
```

This command verifies all decoded pixels and metadata after writing. Keep a
stable copy or commit of INPUT before overwriting. Programmatic opt-in:
`export_effect_bank(effects, path, compressed=True)`.

Codec v1 requires the matching firmware branch `codex/light-rle`. The importer
accepts raw and compressed headers. The editor's existing capacity UI still
reports raw size; this trial does not silently change normal UI export behavior.
The compressed export checks the stored size against the bank capacity.

Format and hardware profiling instructions are in the firmware's RLE_TEST.md.
