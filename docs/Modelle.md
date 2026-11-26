# Modelle

[Handbuch](Handbuch.md) · [Installation](Installation.md) · [Erste Schritte](Erste-Schritte.md) · **Modelle** ·
[Funktionen](Funktionen.md) · [Befehle](Befehle.md)

Modelle liegen in `KI-Modelle/` im Programm-Ordner (anderer Ort: Config `modelle_ordner`), ein Bereich je Art,
darin ein Ordner je Modell. Jeder Bereich enthält eine `Anleitung.txt`. `/einrichten modelle` lädt die Modelle der
Motoren auf Wunsch – mit Lizenz und Größe vorab, aus der offiziellen Quelle und mit fester SHA-256.

```
KI-Modelle/
├─ KI-Chat/Chatmodell/            Sprachmodelle (eigener GGUF-Motor)
├─ KI-Vision/Sehmodell/           Bildbeschreiber (.gguf + mmproj)
├─ KI-Bild/BildgenKI/             Krea 2 (Ordner Krea2) und weitere Bild-Modelle
├─ KI-Video/Videomodell/          SD-Video, LTX-2.5
├─ KI-Stimme/Stimmmodell/         Qwen3-TTS
├─ KI-Musik/Musikmodell/          ACE-Step 1.5
├─ KI-Gedaechtnis/Suchmodell/     Embedding fürs Gedächtnis (Sortiermodell/: Reranker)
├─ KI-Portraet/Portraetmodell/    LivePortrait
├─ KI-Bewegung/Bewegungsmodell/   SprongMotion-14M
└─ KI-Wache/Vorpruefer/           Vorprüfer der Wache
```

| Was | Wie |
|---|---|
| Sprachmodell | `/model huggingface` (nur ladbare Modelle ab 4B, SHA-256, fortsetzbar); eine `mmproj-*.gguf` daneben = das Modell sieht |
| Bildbeschreiber | kleines sehendes Modell für Modelle ohne Sehen, Wahl mit `/vision` (z. B. Qwen3-VL-4B) |
| Gedächtnis | `/embeddings`; empfohlen EmbeddingGemma 2 (findet auch Bilder, Videos, Ton) |
| Krea 2 | Modell (`.safetensors`/`.gguf`), `text_encoders/`, `vae/`, `tokenizer/`; `/bildmodel` |
| Weitere Bild-Modelle | je Modell ein Unterordner, Text-Encoder und VAE geteilt; Bauart wird an der Datei erkannt |
| Video | je Modell ein Unterordner; `/video modell` |
| Stimme · Musik · Porträt | über `/einrichten modelle`, jede Datei vor dem ersten Laden per SHA-256 geprüft |
| Cloud | `/model` → „Cloud-Anbieter hinzufügen“ |
| Ollama · LM Studio · Jan · llama.cpp-Server · vLLM | laufen separat und erscheinen von selbst |

## Video-Modelle selbst laden

Diese Dateien lädst du selbst und vergleichst Größe und SHA-256 (`certutil -hashfile <Datei> SHA256`).

**Wan 2.2** (in `VideoGen/` im Programm-Ordner, je Modell ein Unterordner; `text_encoders/`, `tokenizer/` und
`tae/` dürfen geteilt oben liegen):

| Datei | Quelle | SHA-256 |
|---|---|---|
| `Wan2_2-TI2V-5B-Turbo-Q4_K_M.gguf` | [hum-ma/Wan2.2-TI2V-5B-Turbo-GGUF](https://huggingface.co/hum-ma/Wan2.2-TI2V-5B-Turbo-GGUF) | `13b3bee8fcafd9f9a3778ed1a0ca973f4a7d152094ea305f8384b0abd7e1ad51` |
| `Wan2.2-TI2V-5B-Q4_K_M.gguf` | [QuantStack/Wan2.2-TI2V-5B-GGUF](https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF) | `95b19697b7f98e65b0a543640e9ca7b4dfec32e2a6e3731e8e10708be52655e2` |
| `Wan2.2-Fun-5B-InP-Q8_0.gguf` | [QuantStack/Wan2.2-Fun-5B-InP-GGUF](https://huggingface.co/QuantStack/Wan2.2-Fun-5B-InP-GGUF) | `c88969a140da94176d8af263d031d03b83a7b4783108c73e828a69b9a8873d7f` |
| `Wan2.2_VAE.safetensors` | ebenda (`vae/`) | `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156` |
| `umt5-xxl-encoder-Q8_0.gguf` | [city96/umt5-xxl-encoder-gguf](https://huggingface.co/city96/umt5-xxl-encoder-gguf) | `2521d4de0bf9e1cc6549866463ceae85e4ec3239bc6063f7488810be39033bbc` |
| `tokenizer.json` | [alibaba-pai/Wan2.2-Fun-5B-InP](https://huggingface.co/alibaba-pai/Wan2.2-Fun-5B-InP) (`google/umt5-xxl/`) | `6e197b4d3dbd71da14b4eb255f4fa91c9c1f2068b20a2de2472967ca3d22602b` |
| `spiece.model` | ebenda | `e3909a67b780650b35cf529ac782ad2b6b26e6d1f849d3fbb6a872905f452458` |
| `taew2_2_super.safetensors` (optional, schneller) | [madebyollin/taehv](https://github.com/madebyollin/taehv/tree/main/safetensors) | `cf6d8bdadb77d9f9741ae28b5b4659bb74cf9b0be6d08fc4afed3cb50c39364c` |

**SD-Video** (in `KI-Modelle/KI-Video/Videomodell/`): ein ganzer Bild-Checkpoint (SD 1.5 oder SDXL) im eigenen
Unterordner, ein Bewegungs-Modul in `bewegung/` und `vocab.json` + `merges.txt` von
[openai/clip-vit-large-patch14](https://huggingface.co/openai/clip-vit-large-patch14) in `tokenizer_clip/`.
NemiCLI prüft die offiziellen Bewegungs-Module vor dem ersten Laden per SHA-256:

| Für | Datei | Quelle |
|---|---|---|
| SD 1.5, 4 oder 8 Schritte | `animatediff_lightning_4step_comfyui.safetensors` · `…_8step_…` | [ByteDance/AnimateDiff-Lightning](https://huggingface.co/ByteDance/AnimateDiff-Lightning) |
| SD 1.5, 25 Schritte | `v3_sd15_mm.ckpt` | [guoyww/animatediff](https://huggingface.co/guoyww/animatediff) |
| SDXL | `hsxl_temporal_layers.f16.safetensors` | [hotshotco/Hotshot-XL](https://huggingface.co/hotshotco/Hotshot-XL) |
| SDXL (Beta) | `mm_sdxl_v10_beta.ckpt` | [guoyww/animatediff](https://huggingface.co/guoyww/animatediff) |

`.ckpt`-Dateien werden nur mit passender Prüfsumme als reine Gewichte gelesen und einmal nach `.safetensors`
umgeschrieben.

## SprongMotion

Die Figur im Terminal-Fenster bewegt sich mit [SprongMotion-14M](https://huggingface.co/SegerEnd/SprongMotion-14M)
(Apache-2.0). Das Setup bringt Modell und Laufzeit mit und legt sie beim ersten Start geprüft ab; ohne Setup lädt
`/bewegung holen` die Laufzeit und `/bewegung einrichten` übernimmt das Modell aus dem Download-Ordner.

## TensorRT-RTX

NVIDIA TensorRT for RTX ist nicht Teil von NemiCLI (eigene Lizenz). Wer es nutzen will, lädt das SDK selbst und
legt den entpackten Ordner nach `Models/TensorRT-RTX/`. NemiCLI nutzt es dann für das Einlesen langer Prompts
(`trt_chat`), Krea 2 (`bild_trt`) und Wan 2.2 (`video_trt`); jeder Teil ist einzeln abschaltbar. `/systemcheck`
zeigt den Stand.
